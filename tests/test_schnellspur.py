"""Sprach-Schnellspur: kompakter Prompt, Kernwerkzeuge, Freischalten, Sofort-Reaktion, kuerzerer Verlauf."""

import json
import types

from jarvis import schnellspur
from tests.helpers import B, Stream, make_agent


def _names(agent):
    return {d.get("name") for d in agent._request()["tools"]}


def test_gespraech_nutzt_kompakten_prompt_und_kernwerkzeuge():
    a = make_agent(channel="web")
    req = a._request()
    names = _names(a)
    assert names <= schnellspur.CORE_TOOLS | {"memory"} and "mehr_faehigkeiten" in names
    assert "auftrag_aktualisieren" not in names and "unternehmer_strategie" not in names
    assert "auftrag_anlegen" in names  # Laufzeitfähigkeit: gesprochene Aufträge ohne Umweg erfassen
    assert "Lead-Manager" not in req["system"][0]["text"] and "wie ein Mensch" in req["system"][0]["text"]
    full = len(a.system) + len(json.dumps([t.definition() for t in a.tools.values()]))
    fast = len(req["system"][0]["text"]) + len(json.dumps(req["tools"]))
    assert fast < full * 0.5


def test_terminal_und_hintergrund_bleiben_voll():
    for channel in ("text", "hintergrund"):
        a = make_agent(channel=channel)
        assert "auftrag_aktualisieren" in _names(a) and "mehr_faehigkeiten" not in _names(a)


def test_abschaltbar(monkeypatch):
    monkeypatch.setenv("JARVIS_SCHNELLSPUR", "false")
    a = make_agent(channel="web")
    assert "auftrag_aktualisieren" in _names(a) and "Lead-Manager" in a._request()["system"][0]["text"]


def test_mehr_faehigkeiten_schaltet_frei_und_laeuft_aus():
    a = make_agent(channel="web")
    a._turn_no = 5
    out = a._execute(types.SimpleNamespace(id="x", name="mehr_faehigkeiten", input={"grund": "Upwork"}))
    assert "Freigeschaltet" in out["content"]
    assert "auftrag_aktualisieren" in _names(a) and "Lead-Manager" in a._request()["system"][0]["text"]
    a._turn_no = 7
    assert "auftrag_aktualisieren" in _names(a)
    a._turn_no = 8
    assert "auftrag_aktualisieren" not in _names(a)


def test_benutzte_werkzeuge_bleiben_definiert():
    a = make_agent(channel="web")
    a.messages = [{"role": "user", "content": "x"},
                  {"role": "assistant", "content": [B(type="tool_use", id="1", name="auftrag_aktualisieren", input={})]},
                  {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "ok"}]}]
    assert "auftrag_aktualisieren" in _names(a)


def test_sofort_reaktion_vor_werkzeug():
    said = []
    calls = {"n": 0}

    def script(text):
        return Stream(B(stop_reason="end_turn", content=[B(type="text", text="Fertig.")]), ["Fertig."])

    a = make_agent(channel="web", script=script)

    def stream(**kw):
        calls["n"] += 1
        if calls["n"] == 1:  # Modell ruft sofort ein Werkzeug, ohne vorher etwas zu sagen
            return Stream(B(stop_reason="tool_use", content=[B(type="tool_use", id="t1", name="wetter",
                                                               input={"ort": "Berlin"})]), [])
        return Stream(B(stop_reason="end_turn", content=[B(type="text", text="Es regnet.")]), ["Es regnet."])

    a.client = B(beta=B(messages=B(stream=stream)))
    a.tools["wetter"].handler = lambda ort="", tage=1: "Regen"
    a.ask("Wie wird das Wetter?", on_sentence=said.append)
    assert said[0] in ("Ich schau kurz nach dem Wetter.",) and said[-1] == "Es regnet."
    assert sum(1 for s in said if "Wetter" in s) == 1


def test_keine_sofort_reaktion_wenn_schon_gesprochen_oder_stilles_werkzeug():
    assert schnellspur.filler_for(["rechnen"]) is None
    assert schnellspur.filler_for(["mehr_faehigkeiten"]) is None
    assert schnellspur.filler_for(["kalender_termine"]) in ("Moment, ich schau kurz in deinen Kalender.",
                                                            "Sekunde, ich check deinen Kalender.")
    a = make_agent(channel="web")
    said = []
    a._say, a._filler_done, a._partial = said.append, False, ["Klar, "]
    a._job = {"id": "j"}
    a._speak_filler(["wetter"])
    assert said == []


def test_kuerzerer_verlauf_im_gespraech():
    a = make_agent(channel="web")
    for i in range(12):
        a.messages += [{"role": "user", "content": f"frage {i}"}, {"role": "assistant", "content": "antwort"}]
    a._trim_context()
    assert sum(1 for m in a.messages if m["role"] == "user") == 8
