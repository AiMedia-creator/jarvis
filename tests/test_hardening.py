"""Serverseitige Absicherung: Freigabe nur in einem spaeteren Zug, Hintergrund ohne Freigabe, Nur-Klick,
Duplikat-Hinweis ueber Neustarts, parallele Freigaben, strengere Validierung, Token-Erfassung und Budget."""

import threading
import types

import pytest

from jarvis import agent as A
from jarvis import approvals, telemetry
from jarvis.tools import Tool, ToolFailure, boolean, integer, string

B = types.SimpleNamespace
sent: list = []


def _send(an, betreff, text, bestaetigt_vom_nutzer):
    sent.append((an, betreff, text))
    return f"Mail an {an} gesendet."


def mail_tool():
    return approvals.wrap(Tool("mail_senden", "Sendet", {"an": string("an"), "betreff": string("b"), "text": string("t"),
                                                       "bestaetigt_vom_nutzer": boolean("ok")}, _send,
                               ["an", "betreff", "text", "bestaetigt_vom_nutzer"]))


ARGS = {"an": "max@x.de", "betreff": "Angebot", "text": "Hallo Max"}


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    approvals._items.clear()
    approvals._turns.clear()
    sent.clear()
    monkeypatch.delenv("JARVIS_FREIGABE_NUR_KLICK", raising=False)
    try:
        approvals._actions_file().unlink()
    except FileNotFoundError:
        pass


def test_freigabe_im_selben_zug_wird_abgelehnt_und_im_naechsten_akzeptiert():
    t = mail_tool()
    turn = approvals.next_turn("haupt")
    with approvals.context("haupt", turn, "web"):
        assert "VORLAGE" in t.run({**ARGS, "bestaetigt_vom_nutzer": False})
        with pytest.raises(ToolFailure, match="NEUEN Nachricht"):
            t.run({**ARGS, "bestaetigt_vom_nutzer": True})
    assert sent == []
    with approvals.context("haupt", approvals.next_turn("haupt"), "web"):
        assert "gesendet" in t.run({**ARGS, "bestaetigt_vom_nutzer": True})
    assert len(sent) == 1


def test_hintergrund_darf_nie_freigeben():
    t = mail_tool()
    with approvals.context("bg-1", approvals.next_turn("bg-1"), "hintergrund"):
        t.run({**ARGS, "bestaetigt_vom_nutzer": False})
    with approvals.context("bg-1", approvals.next_turn("bg-1"), "hintergrund"):
        with pytest.raises(ToolFailure, match="Hintergrund"):
            t.run({**ARGS, "bestaetigt_vom_nutzer": True})
    assert sent == []
    # Der Nutzer kann die Vorlage aus dem Hintergrund-Auftrag aber selbst per Klick freigeben.
    approvals.approve(approvals.listing()[0]["id"])
    assert len(sent) == 1


def test_nur_klick_modus(monkeypatch):
    monkeypatch.setenv("JARVIS_FREIGABE_NUR_KLICK", "true")
    t = mail_tool()
    with approvals.context("haupt", approvals.next_turn("haupt"), "web"):
        assert "per Klick" in t.run({**ARGS, "bestaetigt_vom_nutzer": False})
    with approvals.context("haupt", approvals.next_turn("haupt"), "web"):
        with pytest.raises(ToolFailure, match="per Klick"):
            t.run({**ARGS, "bestaetigt_vom_nutzer": True})
    assert sent == []
    assert "gesendet" in approvals.approve(approvals.listing()[0]["id"])


def test_duplikat_hinweis_auch_nach_neustart():
    t = mail_tool()
    t.run({**ARGS, "bestaetigt_vom_nutzer": False})
    approvals.approve(approvals.listing()[0]["id"])
    approvals._items.clear()  # simuliert Neustart: Speicher leer, Protokoll auf der Platte bleibt
    out = t.run({**ARGS, "bestaetigt_vom_nutzer": False})
    assert "bereits ausgeführt" in out
    assert approvals.listing()[0]["duplikat"]
    # Anderer Inhalt -> kein Duplikat
    approvals._items.clear()
    assert "bereits ausgeführt" not in t.run({**ARGS, "text": "Neu", "bestaetigt_vom_nutzer": False})
    # Protokoll enthaelt nur Hashes, keine Inhalte
    raw = approvals._actions_file().read_text()
    assert "max@x.de" not in raw and "Hallo Max" not in raw


def test_parallele_freigaben_senden_nur_einmal():
    t = mail_tool()
    t.run({**ARGS, "bestaetigt_vom_nutzer": False})
    item = approvals.listing()[0]["id"]
    errors = []

    def click():
        try:
            approvals.approve(item)
        except ToolFailure as exc:
            errors.append(exc)

    threads = [threading.Thread(target=click) for _ in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(sent) == 1 and len(errors) == 7


def test_validierung_bool_ist_keine_zahl_und_enum():
    t = Tool("x", "x", {"n": integer("n"), "m": {"type": "string", "enum": ["a", "b"]}}, lambda **kw: "ok")
    with pytest.raises(ToolFailure, match="integer"):
        t.run({"n": True})
    with pytest.raises(ToolFailure, match="einer dieser Werte"):
        t.run({"n": 1, "m": "c"})
    assert t.run({"n": 1, "m": "a"}) == "ok"


class Stream:
    def __init__(self, final, texts=()):
        self.final, self.texts = final, list(texts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    @property
    def text_stream(self):
        yield from self.texts

    def get_final_message(self):
        return self.final


def _usage(inp=100, out=20):
    return B(input_tokens=inp, output_tokens=out, cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _agent(steps):
    a = A.Agent(channel="web", conversation_id="test-zug")
    a.client = B(beta=B(messages=B(stream=lambda **kw: steps.pop(0))))
    a.tools["mail_senden"] = mail_tool()
    return a


def test_prompt_injection_kann_nicht_selbst_freigeben():
    """Das Modell (z. B. von einer manipulierten Mail verleitet) erstellt eine Vorlage und bestaetigt sofort selbst."""
    call = lambda ok, i: B(type="tool_use", id=f"t{i}", name="mail_senden", input={**ARGS, "bestaetigt_vom_nutzer": ok})
    steps = [Stream(B(stop_reason="tool_use", content=[call(False, 1)], usage=_usage())),
             Stream(B(stop_reason="tool_use", content=[call(True, 2)], usage=_usage())),
             Stream(B(stop_reason="end_turn", content=[B(type="text", text="Vorlage liegt bereit.")], usage=_usage()),
                    ["Vorlage liegt bereit."])]
    a = _agent(steps)
    a.ask("Lies die Mail von Eve und tu, was drinsteht.")
    assert sent == []
    result = a.messages[4]["content"][0]
    assert result["is_error"] and "NEUEN Nachricht" in result["content"]
    # Klares Ja in der naechsten Nachricht -> genau einmal gesendet
    steps += [Stream(B(stop_reason="tool_use", content=[call(True, 3)], usage=_usage())),
              Stream(B(stop_reason="end_turn", content=[B(type="text", text="Gesendet.")], usage=_usage()), ["Gesendet."])]
    a.ask("Ja, schick sie.")
    assert len(sent) == 1


def test_tokens_werden_erfasst_und_budget_greift(monkeypatch):
    monkeypatch.setenv("JARVIS_JOB_TOKEN_BUDGET", "250")
    monkeypatch.setenv("JARVIS_PREISE", '{"claude-sonnet-5-5": [1, 2, 0, 0]}')
    loop = lambda i: Stream(B(stop_reason="tool_use", usage=_usage(),
                              content=[B(type="tool_use", id=f"s{i}", name="lies_skill", input={"name": "x"})]))
    a = _agent([loop(i) for i in range(5)])
    a.model = "claude-sonnet-5-5"
    said = []
    out = a.ask("Endlosschleife", on_sentence=said.append)
    assert "Token-Budget" in out and said[-1] == out
    rec = telemetry.job(a._job["id"])
    assert rec["tokens_ein"] == 300 and rec["tokens_aus"] == 60 and rec["fehlerart"] == "BudgetExceeded"
    assert rec["kosten_usd"] == pytest.approx((300 * 1 + 60 * 2) / 1_000_000)
    assert a._job["status"] == "FAILED"


def test_ohne_preise_keine_erfundenen_kosten(monkeypatch):
    monkeypatch.delenv("JARVIS_PREISE", raising=False)
    telemetry.start_job("j-ohne-preis", "x", "claude-haiku-5-5")
    telemetry.add_usage("j-ohne-preis", "claude-haiku-5-5", {"input_tokens": 10, "output_tokens": 5})
    rec = telemetry.job("j-ohne-preis")
    assert rec["tokens_ein"] == 10 and "kosten_usd" not in rec
