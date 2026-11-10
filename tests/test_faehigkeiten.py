"""Laufzeitfähigkeiten: Registry, Lauf als Job (vom Gespräch getrennt), Status, Abbruch, Wiederholung."""

import threading

import pytest

from jarvis import faehigkeiten, jobs, orders, runtime
from jarvis.tools import ToolFailure


@pytest.fixture(autouse=True)
def daten(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    yield


def test_registry_beschreibt_faehigkeit_vollstaendig():
    d = {x["name"]: x for x in faehigkeiten.definitions()}["auftrag_anlegen"]
    assert d["berechtigung"] == "lokal" and d["abbrechbar"] is True
    assert d["eingabe"]["required"] == ["titel"] and "prioritaet" in d["eingabe"]["properties"]


def test_gesprochener_auftrag_landet_in_auftragsverwaltung_und_ist_sichtbar():
    start = runtime.bus.since(0)[1]
    out = faehigkeiten.run_tool("auftrag_anlegen", {"titel": "Angebot Weber schicken", "kunde": "Weber",
                                                    "prioritaet": "", "frist": ""})
    order = orders.listing()["auftraege"][0]
    assert order["titel"] == "Angebot Weber schicken" and order["status"] == "eingang" and order["kunde"] == "Weber"
    assert order["id"] in out and order["quelle"].startswith("jarvis:")
    events = [e for e in runtime.bus.since(start)[0] if e["typ"] == "faehigkeit"]
    assert [e["phase"] for e in events] == ["gestartet", "fertig"]
    assert events[-1]["lauf"]["ergebnis"]["auftrag_id"] == order["id"]
    job = jobs.get(events[-1]["job_id"])
    assert job["status"] == "COMPLETED" and job["id"] in order["jobs"]


def test_ungueltige_eingaben_werden_abgelehnt():
    with pytest.raises(ToolFailure):
        faehigkeiten.run_tool("auftrag_anlegen", {"kunde": "ohne Titel"})
    with pytest.raises(ToolFailure):
        faehigkeiten.run_tool("auftrag_anlegen", {"titel": "x", "prioritaet": "sofort!"})


def test_lauf_ist_vom_gespraech_getrennt_und_abbrechbar():
    gate, started = threading.Event(), threading.Event()

    def langsam(args, lauf):
        started.set()
        gate.wait(5)
        lauf.pruefen()
        return {"text": "fertig"}

    faehigkeiten.register(faehigkeiten.Faehigkeit("test_langsam", "Langsam", "Test", {}, [], "lesen", langsam, warten=0.05))
    out = faehigkeiten.run_tool("test_langsam", {})
    assert "läuft noch als Job" in out  # Werkzeugaufruf kehrt zurück, die Sitzung bleibt frei
    started.wait(2)
    run = faehigkeiten.recent(1)[0]
    assert run["status"] == "RUNNING" and run["abbrechbar"]
    # Ein Abbruch des Gesprächs (Agent) berührt den Lauf nicht; nur der ausdrückliche Abbruch stoppt ihn.
    runtime.cancel_conversation("haupt")
    assert jobs.get(run["id"])["status"] == "RUNNING"
    assert faehigkeiten.cancel(run["id"]) is True
    gate.set()
    assert jobs.get(run["id"])["status"] == "CANCELLED"
    again = faehigkeiten.retry(run["id"])
    assert again["faehigkeit"] == "test_langsam" and again["id"] != run["id"]


def test_externe_faehigkeit_braucht_freigabe():
    with pytest.raises(ValueError):
        faehigkeiten.register(faehigkeiten.Faehigkeit("test_extern", "Extern", "x", {}, [], "extern", lambda a, l: {}))
