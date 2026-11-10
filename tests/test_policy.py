"""Werkzeug-Policy: Klassifikation, doppelte Zustellung, parallele Aufrufe, sichere Wiederaufnahme, Grenzen."""

import threading
import time
import types

import pytest

from jarvis import agent as A
from jarvis import persist, policy
from jarvis.tools import Tool, ToolFailure, string

B = types.SimpleNamespace


@pytest.fixture(autouse=True)
def clean_ledger():
    with persist._connect() as c:
        c.execute("DELETE FROM tool_ledger")
    policy._turn_writes.clear()


def counting_tool(name="aufgabe_hinzufuegen", fail=None, delay=0.0):
    calls = []

    def handler(titel):
        calls.append(titel)
        time.sleep(delay)
        if fail:
            raise fail
        return f"Aufgabe '{titel}' angelegt."

    return Tool(name, "Test", {"titel": string("t")}, handler, ["titel"]), calls


def test_klassifikation():
    assert policy.kind("mails_suchen") == "read"
    assert policy.kind("aufgabe_hinzufuegen") == "write"
    assert policy.kind("programm_oeffnen") == "local"
    assert policy.kind("irgendein_neues_werkzeug") == "write"  # unbekannt = vorsichtig
    assert policy.kind("memory", {"command": "view"}) == "read"
    assert policy.kind("memory", {"command": "delete"}) == "write"
    ext = Tool("mail_senden", "", {"bestaetigt_vom_nutzer": {"type": "boolean"}}, lambda **kw: "")
    assert policy.kind("mail_senden", {}, ext) == "external"


def test_doppelte_zustellung_wird_nur_einmal_ausgefuehrt():
    tool, calls = counting_tool()
    assert "angelegt" in policy.execute(tool, {"titel": "Angebot nachfassen"}, conversation="c", turn=1)
    second = policy.execute(tool, {"titel": "Angebot nachfassen"}, conversation="c", turn=2)
    assert "Bereits ausgeführt" in second and calls == ["Angebot nachfassen"]
    policy.execute(tool, {"titel": "Andere Aufgabe"}, conversation="c", turn=2)
    assert len(calls) == 2  # andere Angaben = neue Aktion


def test_parallele_zustellung_fuehrt_nur_einmal_aus():
    tool, calls = counting_tool(delay=0.2)
    outcomes = []

    def deliver():
        try:
            outcomes.append(policy.execute(tool, {"titel": "Gleich"}, conversation="c", turn=1))
        except ToolFailure as exc:
            outcomes.append(str(exc))

    threads = [threading.Thread(target=deliver) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == ["Gleich"] and len(outcomes) == 6
    assert sum("läuft bereits" in o or "Bereits ausgeführt" in o for o in outcomes) == 5


def test_absturz_mitten_in_der_aktion_wird_unklar_und_nie_wiederholt():
    tool, calls = counting_tool()
    fp = policy.fingerprint(tool.name, {"titel": "Rechnung"})
    with persist._connect() as c:  # Zustand wie nach einem Absturz: STARTED ohne Ende
        c.execute("INSERT INTO tool_ledger (id,fingerprint,tool,kind,status,source,conversation,turn,started) "
                  "VALUES ('x',?,?,'write','STARTED','web','c',1,?)", (fp, tool.name, time.time() - 30))
    assert policy.restore() == 1
    with pytest.raises(ToolFailure, match="unklar"):
        policy.execute(tool, {"titel": "Rechnung"}, conversation="c", turn=2)
    assert calls == []
    assert policy.recent(1)[0]["status"] == "UNCLEAR"


def test_netzfehler_mitten_drin_ist_unklar_validierungsfehler_nicht():
    tool, calls = counting_tool(fail=ConnectionError("reset"))
    with pytest.raises(ToolFailure, match="Ausgang unklar"):
        policy.execute(tool, {"titel": "A"}, conversation="c", turn=1)
    with pytest.raises(ToolFailure, match="unklar"):
        policy.execute(tool, {"titel": "A"}, conversation="c", turn=2)
    assert len(calls) == 1
    bad, bad_calls = counting_tool(fail=ToolFailure("Google nicht verbunden"))
    for turn in (1, 2):
        with pytest.raises(ToolFailure, match="nicht verbunden"):
            policy.execute(bad, {"titel": "B"}, conversation="c", turn=turn)
    assert len(bad_calls) == 2  # sicherer Fehler vor der Wirkung: erneuter Versuch erlaubt


def test_schema_validierung_vor_jeder_wirkung():
    tool, calls = counting_tool()
    with pytest.raises(ToolFailure, match="Unbekanntes Argument"):
        policy.execute(tool, {"titel": "x", "loesche_alles": True}, conversation="c", turn=1)
    assert calls == []


def test_minimale_rechte_und_grenzen():
    opener = Tool("programm_oeffnen", "", {"name": string("n")}, lambda name: "geöffnet", ["name"])
    with pytest.raises(ToolFailure, match="Hintergrund"):
        policy.execute(opener, {"name": "Safari"}, channel="hintergrund", conversation="bg", turn=1)
    assert policy.execute(opener, {"name": "Safari"}, channel="web", conversation="c", turn=1) == "geöffnet"
    tool, calls = counting_tool()
    for i in range(policy.MAX_WRITES_PER_TURN):
        policy.execute(tool, {"titel": f"T{i}"}, conversation="c", turn=7)
    with pytest.raises(ToolFailure, match="Grenze"):
        policy.execute(tool, {"titel": "zu viel"}, conversation="c", turn=7)
    assert len(calls) == policy.MAX_WRITES_PER_TURN


def test_lesen_wird_nicht_protokolliert_oder_blockiert():
    reader = Tool("mails_suchen", "", {"suche": string("s")}, lambda suche: "3 Mails", [])
    for _ in range(3):
        assert policy.execute(reader, {"suche": "is:unread"}, conversation="c", turn=1) == "3 Mails"
    assert policy.recent() == []


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


def test_agent_fuehrt_wiederholten_aufruf_im_selben_zug_nur_einmal_aus():
    tool, calls = counting_tool()
    call = lambda i: B(type="tool_use", id=f"a{i}", name="aufgabe_hinzufuegen", input={"titel": "Kunde anrufen"})
    steps = [Stream(B(stop_reason="tool_use", content=[call(1)], usage=None)),
             Stream(B(stop_reason="tool_use", content=[call(2)], usage=None)),
             Stream(B(stop_reason="end_turn", content=[B(type="text", text="Erledigt.")], usage=None), ["Erledigt."])]
    a = A.Agent(channel="web", conversation_id="policy-test")
    a.client = B(beta=B(messages=B(stream=lambda **kw: steps.pop(0))))
    a.tools["aufgabe_hinzufuegen"] = tool
    a.ask("Leg die Aufgabe an")
    assert calls == ["Kunde anrufen"]
    assert "Bereits ausgeführt" in a.messages[4]["content"][0]["content"]
