"""Gespraechs- und Job-Isolation, Hintergrundjobs, Freigabe-Wartezustand, Telemetrie pro Job."""

import json
import threading
import time
import urllib.request

import pytest

from helpers import B, Stream, make_agent
from jarvis import approvals, jobs, runtime, telemetry, webui
from jarvis.tools import Tool, boolean, string


@pytest.fixture
def rt(monkeypatch):
    """Runtime mit simulierten Agenten (Haupt- und Hintergrundgespraeche)."""
    main = make_agent()
    monkeypatch.setattr(webui, "_agent", main)
    monkeypatch.setattr(webui, "make_agent", lambda channel, conv: make_agent(channel, conv))
    runtime.set_factory(webui._factory)
    approvals._items.clear()
    return main


def wait_status(job_id, statuses, timeout=6):
    end = time.time() + timeout
    while time.time() < end:
        j = jobs.get(job_id)
        if j and j["status"] in statuses:
            return j
        time.sleep(0.05)
    raise AssertionError(f"Job {job_id} hat {statuses} nicht erreicht: {jobs.get(job_id)}")


def bus_events(job_id):
    return [e for e in runtime.bus.since(0)[0] if e.get("job_id") == job_id]


# 1) zwei getrennte Fragen hintereinander
def test_zwei_fragen_nacheinander_getrennt(rt):
    srv = webui.ThreadingHTTPServer(("127.0.0.1", 0), webui.Handler)
    port = srv.server_address[1]
    webui.ALLOWED_HOSTS = {f"127.0.0.1:{port}"}
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    def ask(text):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/ask", data=json.dumps({"text": text}).encode(),
                                     headers={"X-Jarvis": "1", "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return [json.loads(line) for line in r]

    e1, e2 = ask("Wie ist das Wetter?"), ask("Was macht HubSpot?")
    srv.shutdown()
    j1, j2 = e1[0]["job_id"], e2[0]["job_id"]
    assert j1 != j2
    assert all(e["job_id"] == j1 and e["conversation_id"] == "haupt" for e in e1)
    assert all(e["job_id"] == j2 for e in e2)
    text2 = "".join(e["text"] for e in e2 if e["typ"] == "token")
    assert text2 == "Antwort zu HubSpot." and "Wetter" not in text2


# 2) zwei parallele Jobs ohne Vermischung
def test_zwei_parallele_jobs(rt):
    a = runtime.start_background("Recherche langsam Alpha")
    b = runtime.start_background("Recherche langsam Beta")
    assert a["conversation"] != b["conversation"]
    wait_status(a["id"], {jobs.COMPLETED}), wait_status(b["id"], {jobs.COMPLETED})
    ta = "".join(e["text"] for e in bus_events(a["id"]) if e["typ"] == "token")
    tb = "".join(e["text"] for e in bus_events(b["id"]) if e["typ"] == "token")
    assert "Alpha-0" in ta and "Beta" not in ta
    assert "Beta-0" in tb and "Alpha" not in tb
    assert jobs.get(a["id"])["ergebnis"].startswith("Alpha-0") and jobs.get(b["id"])["ergebnis"].startswith("Beta-0")


# 4) Abbruch waehrend LLM-Streaming trifft nur den gemeinten Job
def test_abbruch_trifft_nur_einen_job(rt):
    a = runtime.start_background("Bericht langsam Eins")
    b = runtime.start_background("Bericht langsam Zwei")
    time.sleep(0.4)
    t0 = time.time()
    assert runtime.cancel_job(a["id"])
    wait_status(a["id"], {jobs.CANCELLED}, timeout=1.5)
    assert time.time() - t0 < 1.5
    assert wait_status(b["id"], {jobs.COMPLETED})["status"] == jobs.COMPLETED
    assert any(e["typ"] == "abgebrochen" for e in bus_events(a["id"]))


# 6) Hintergrundjob waehrend aktiver Unterhaltung
def test_hintergrundjob_waehrend_gespraech(rt):
    bg = runtime.start_background("Pruefe alle Kontakte langsam Hintergrund")
    time.sleep(0.2)
    out = []
    job = runtime.run("haupt", "Wie spaet ist es?", sink=out.append)
    assert job["status"] == jobs.COMPLETED and jobs.get(bg["id"])["status"] == jobs.RUNNING  # Gespraech wartet nicht
    assert all(e["job_id"] == job["id"] for e in out)
    assert "Hintergrund" not in str(rt.messages)  # Verlauf bleibt sauber
    wait_status(bg["id"], {jobs.COMPLETED})


# 7) Job mit WAITING_FOR_APPROVAL
def test_job_wartet_auf_freigabe_und_wird_abgeschlossen(rt, monkeypatch):
    sent = []
    mail = approvals.wrap(Tool("mail_senden", "Sendet", {"an": string("an"), "bestaetigt_vom_nutzer": boolean("ok")},
                               lambda an, bestaetigt_vom_nutzer: sent.append(an) or "gesendet"))

    def script(text):
        call = B(type="tool_use", id="t1", name="mail_senden", input={"an": "max@x.de", "bestaetigt_vom_nutzer": False})
        return Stream(B(stop_reason="tool_use", content=[call]), [])

    def factory(channel, conv):
        ag = make_agent(channel, conv, script=script)
        ag.tools["mail_senden"] = mail
        return ag

    monkeypatch.setattr(webui, "make_agent", factory)
    bg = runtime.start_background("Bereite Mail an Max vor")
    job = wait_status(bg["id"], {jobs.WAITING})
    step = job["schritte"][0]
    assert step["status"] == jobs.WAITING and step["berechtigung"] == "freigabe" and step.get("freigabe_id")
    assert sent == []
    approvals.approve(step["freigabe_id"])
    assert sent == ["max@x.de"]
    assert jobs.get(bg["id"])["status"] == jobs.COMPLETED and jobs.get(bg["id"])["schritte"][0]["status"] == jobs.COMPLETED


# 8) Telemetrie pro Job: richtige Felder, keine Inhalte
def test_telemetrie_pro_job(rt):
    job = runtime.run("haupt", "Geheime Frage zu Umsatzzahlen?", sink=lambda e: None)
    rec = telemetry.job(job["id"])
    assert rec["job"] == job["id"] and rec["conversation"] == "haupt" and rec["modell"].startswith("claude-")
    for key in ("erstes_token_ms", "erster_satz_ms", "llm_ende_ms", "gesamt_ms"):
        assert isinstance(rec[key], int), key
    assert rec["status"] == jobs.COMPLETED and rec["unterbrochen"] is False
    assert telemetry.client_report(job["id"], {"ui_feedback_ms": 85, "stimme_ms": 1200, "boese": "x"})
    rec = telemetry.job(job["id"])
    assert rec["ui_feedback_ms"] == 85 and rec["stimme_ms"] == 1200 and "boese" not in rec
    logged = (telemetry.config.data_dir() / "telemetry.jsonl").read_text(encoding="utf-8")
    assert "Umsatzzahlen" not in logged and "Geheime" not in logged  # keine Inhalte
    bg = runtime.start_background("Lauf langsam Abbruch")
    time.sleep(0.3)
    runtime.cancel_job(bg["id"])
    wait_status(bg["id"], {jobs.CANCELLED})
    time.sleep(0.3)
    rec = telemetry.job(bg["id"])
    assert rec["conversation"].startswith("bg-") and rec["art"] == "hintergrund" and rec["unterbrochen"] is True
