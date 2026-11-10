"""Laufzeit: Token-Streaming, sofortiger Abbruch, parallele Werkzeuge, Timeouts, Fehler, Jobs, Telemetrie."""

import threading
import time
import types

import pytest

from jarvis import agent as A
from jarvis import jobs, telemetry
from jarvis.tools import Tool, ToolFailure, boolean, string

B = types.SimpleNamespace


class Stream:
    """Simulierter Claude-Stream. first_delay = Wartezeit bis zum ersten Token (Claude 'denkt')."""

    def __init__(self, final, texts, delay=0.0, first_delay=0.0):
        self.final, self.texts, self.delay, self.first_delay = final, texts, delay, first_delay
        self.closed = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def close(self):
        self.closed.set()

    @property
    def text_stream(self):
        if self.closed.wait(self.first_delay):
            raise RuntimeError("stream closed")
        for t in self.texts:
            if self.closed.wait(self.delay):
                raise RuntimeError("stream closed")
            yield t

    def get_final_message(self):
        return self.final


def text_final(text):
    return B(stop_reason="end_turn", content=[B(type="text", text=text)])


def tool_final(*calls):
    return B(stop_reason="tool_use", content=[B(type="tool_use", id=f"t{i}", name=n, input=a) for i, (n, a) in enumerate(calls)])


def make_agent(steps, extra_tools=()):
    a = A.Agent(channel="web")
    a.client = B(beta=B(messages=B(stream=lambda **kw: steps.pop(0))))
    for t in extra_tools:
        a.tools[t.name] = t
    return a


def slow_tool(name, seconds, result="ok", fail=False):
    def run(frage=""):
        time.sleep(seconds)
        if fail:
            raise ToolFailure("Dienst nicht erreichbar")
        return f"{name}:{result}"
    return Tool(name, "Test", {"frage": string("x")}, run)


def test_token_streaming_und_erster_satz_frueh():
    tokens, sentences = [], []
    long_first = "Ich habe gerade in deinem Kalender nachgesehen, und heute ist es ruhig. Zweiter Satz."
    a = make_agent([Stream(text_final(long_first), [long_first[i:i + 6] for i in range(0, len(long_first), 6)])])
    a.ask("Kalender?", on_sentence=sentences.append, on_token=tokens.append)
    assert "".join(tokens) == long_first and len(tokens) > 5
    assert sentences[0].endswith(",")  # erster Abschnitt schon am Komma -> Stimme startet frueher
    rec = telemetry.job(jobs.recent(1)[0]["id"])
    assert rec["erstes_token_ms"] is not None and rec["erster_satz_ms"] is not None


def test_abbruch_waehrend_claude_noch_denkt():
    stream = Stream(text_final("x"), ["x"], first_delay=10)
    a = make_agent([stream])
    t0 = time.monotonic()
    threading.Timer(0.3, a.cancel).start()
    with pytest.raises(A.Cancelled):
        a.ask("Hallo")
    assert time.monotonic() - t0 < 1.5 and stream.closed.is_set()


def test_abbruch_waehrend_werkzeug_laeuft():
    tool = slow_tool("langsam", 5)
    a = make_agent([Stream(tool_final(("langsam", {"frage": "a"})), []), Stream(text_final("nie"), ["nie"])], [tool])
    t0 = time.monotonic()
    threading.Timer(0.3, a.cancel).start()
    steps = []
    with pytest.raises(A.Cancelled):
        a.ask("Mach was", on_step=steps.append)
    assert time.monotonic() - t0 < 1.5
    assert steps[-1]["status"] == jobs.CANCELLED
    assert jobs.recent(1)[0]["status"] == jobs.CANCELLED


def test_neue_eingabe_unterbricht_alte_antwort():
    long = Stream(text_final("lang"), ["a. "] * 100, delay=0.1)
    second = Stream(text_final("Neue Antwort."), ["Neue Antwort."])
    a = make_agent([long, second])
    errors = []
    t = threading.Thread(target=lambda: errors.append(pytest.raises(A.Cancelled, a.ask, "Erzähl lange")))
    t.start()
    time.sleep(0.4)
    t0 = time.monotonic()
    out = a.ask("Stopp, was anderes")
    t.join(3)
    assert out == "Neue Antwort." and time.monotonic() - t0 < 1.5
    assert a.messages[1]["content"].endswith("[vom Nutzer unterbrochen]")


def test_unabhaengige_lesewerkzeuge_laufen_parallel():
    t1, t2, t3 = slow_tool("kalender", 0.6), slow_tool("mails", 0.6), slow_tool("deals", 0.6)
    a = make_agent([Stream(tool_final(("kalender", {}), ("mails", {}), ("deals", {})), []),
                    Stream(text_final("Briefing fertig."), ["Briefing fertig."])], [t1, t2, t3])
    steps = []
    t0 = time.monotonic()
    a.ask("Briefing", on_step=steps.append)
    assert time.monotonic() - t0 < 1.3  # parallel ~0.6 s statt 1.8 s
    results = a.messages[2]["content"]
    assert [r["content"] for r in results] == ["kalender:ok", "mails:ok", "deals:ok"]  # richtige Zuordnung
    done = [s for s in steps if s["status"] == jobs.COMPLETED]
    assert {s["werkzeug"] for s in done} == {"kalender", "mails", "deals"} and all(s["berechtigung"] == "lesen" for s in done)


def test_werkzeugfehler_und_timeout_werden_gemeldet(monkeypatch):
    monkeypatch.setenv("JARVIS_TOOL_TIMEOUT", "0.5")
    a = make_agent([Stream(tool_final(("kaputt", {}), ("haengt", {})), []),
                    Stream(text_final("Teilweise erledigt."), ["Teilweise erledigt."])],
                   [slow_tool("kaputt", 0, fail=True), slow_tool("haengt", 5)])
    t0 = time.monotonic()
    out = a.ask("Mach beides")
    assert out == "Teilweise erledigt." and time.monotonic() - t0 < 1.5
    contents = [r["content"] for r in a.messages[2]["content"]]
    assert "Dienst nicht erreichbar" in contents[0] and "Zeitueberschreitung" in contents[1]
    assert jobs.recent(1)[0]["status"] == jobs.COMPLETED


def test_freigabe_nur_vor_riskanter_aktion():
    from jarvis import approvals

    sent = []
    send = approvals.wrap(Tool("mail_senden", "Sendet", {"an": string("an"), "bestaetigt_vom_nutzer": boolean("ok")},
                               lambda an, bestaetigt_vom_nutzer: sent.append(an) or "gesendet"))
    a = make_agent([Stream(tool_final(("suche", {}), ("mail_senden", {"an": "x@y.de", "bestaetigt_vom_nutzer": False})), []),
                    Stream(text_final("Vorlage liegt bereit."), ["Vorlage liegt bereit."])],
                   [slow_tool("suche", 0), send])
    steps = []
    a.ask("Such und bereite Mail vor", on_step=steps.append)
    final = {s["werkzeug"]: s for s in steps if s["status"] != jobs.RUNNING}
    assert final["suche"]["status"] == jobs.COMPLETED and final["suche"]["berechtigung"] == "lesen"
    assert final["mail_senden"]["status"] == jobs.WAITING and final["mail_senden"]["berechtigung"] == "freigabe"
    assert sent == [] and jobs.recent(1)[0]["status"] == jobs.WAITING
