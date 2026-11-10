"""Etappe 4: ein Scheduler mit Lauf-Journal (Coach & Co.) und ehrliches n8n (Outbox, Status, Callback)."""

import datetime
import json
import threading
import time

import pytest
import requests

from jarvis import agent as A
from jarvis import approvals, coach, jobs, orders, scheduler  # noqa: F401  (coach registriert Abläufe)
from jarvis.tools import ToolFailure, n8n

MONDAY_0800 = datetime.datetime(2026, 10, 19, 8, 0)


@pytest.fixture(autouse=True)
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    for module in (scheduler, n8n, orders):
        module._ready.clear()
    monkeypatch.setattr(scheduler, "RETRY_AFTER", 0.0)
    from jarvis.tools import google

    monkeypatch.setattr(google, "is_connected", lambda: True)
    scheduler.stop_loop()
    yield tmp_path
    scheduler.stop_loop()


@pytest.fixture
def background(monkeypatch):
    """Ersetzt den echten Hintergrund-Graphlauf: legt einen Job an und beendet ihn wie vorgegeben."""
    from jarvis import runtime

    calls = []
    outcome = {"status": jobs.COMPLETED, "text": "Drei Hebel: Fokusblock Dienstag. Jetzt sofort: ..."}

    def start(text, tools=None, title=None):
        job = jobs.create(title or text, "bg-test", "hintergrund")
        calls.append({"text": text, "tools": tools, "job": job["id"]})

        def work():
            time.sleep(0.05)
            if outcome["status"] == jobs.COMPLETED:
                jobs.set_result(job, outcome["text"])
            jobs.finish(job, outcome["status"], None if outcome["status"] == jobs.COMPLETED else "APIError: 529 overloaded")

        threading.Thread(target=work, daemon=True).start()
        return {"id": job["id"]}

    monkeypatch.setattr(runtime, "start_background", start)
    return calls, outcome


def test_coach_einmal_pro_tag_auch_nach_neustart(background):
    calls, _ = background
    assert scheduler.tick(MONDAY_0800.replace(hour=7), wait=True) == []  # vor 07:30
    assert scheduler.tick(MONDAY_0800, wait=True) == ["coach:2026-10-19"]
    assert scheduler.get_run("coach:2026-10-19")["status"] == "SUCCEEDED"
    # "Neustart": neuer Prozessbesitzer, gleiche Datenbank -> kein zweiter Lauf
    assert scheduler.tick(MONDAY_0800.replace(hour=9), owner="neuer-prozess", wait=True) == []
    assert scheduler.tick(datetime.datetime(2026, 10, 24, 9, 0), wait=True) == []  # Samstag
    assert len(calls) == 1 and set(calls[0]["tools"]) == set(coach.READ_BUSINESS)
    assert "Aendere, sende oder verschiebe nichts" in calls[0]["text"]


def test_gleichzeitige_prozesse_starten_nur_einen_lauf(background):
    results = []
    barrier = threading.Barrier(4)

    def race(owner):
        barrier.wait()
        results.append(scheduler.claim(scheduler.REGISTRY["coach"], "coach:2026-10-19", {}, owner))

    threads = [threading.Thread(target=race, args=(f"p{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [False, False, False, True]


def test_fehler_ist_nie_erfolg_und_wird_begrenzt_wiederholt(background):
    calls, outcome = background
    outcome["status"] = jobs.FAILED
    scheduler.tick(MONDAY_0800, wait=True)
    run = scheduler.get_run("coach:2026-10-19")
    assert run["status"] == "FAILED" and "529" in run["error"] and run["result"] == ""
    assert scheduler.pending_notes("2026-10-19") == []  # kein Hinweis auf ein "fertiges" Briefing
    outcome["status"] = jobs.COMPLETED
    scheduler.tick(MONDAY_0800.replace(hour=9), wait=True)  # zweiter Versuch
    run = scheduler.get_run("coach:2026-10-19")
    assert run["status"] == "SUCCEEDED" and run["attempts"] == 2
    notes = scheduler.pending_notes("2026-10-19")
    assert len(notes) == 1 and notes[0].startswith("Coach-Briefing von heute liegt vor") and "Fokusblock" in notes[0]
    assert scheduler.pending_notes("2026-10-19") == []  # nur einmal erwähnt
    outcome["status"] = jobs.FAILED
    assert len(calls) == 2


def test_erschoepfte_versuche_bleiben_fehlgeschlagen(background):
    calls, outcome = background
    outcome["status"] = jobs.FAILED
    for hour in (8, 9, 10, 11):
        scheduler.tick(MONDAY_0800.replace(hour=hour), wait=True)
    assert len(calls) == scheduler.REGISTRY["coach"].max_attempts
    assert scheduler.get_run("coach:2026-10-19")["status"] == "FAILED"


def test_absturz_waehrend_lauf_wird_nach_neustart_aufgeraeumt(background):
    wf = scheduler.REGISTRY["coach"]
    assert scheduler.claim(wf, "coach:2026-10-19", {}, "alter-prozess", now=time.time() - 3600)
    assert scheduler.get_run("coach:2026-10-19")["status"] == "STARTED"
    assert scheduler.recover() == 1  # Lease abgelaufen -> FAILED (unterbrochen), nicht SUCCEEDED
    assert scheduler.get_run("coach:2026-10-19")["error"].startswith("Unterbrochen")
    scheduler.tick(MONDAY_0800.replace(hour=9), wait=True)
    assert scheduler.get_run("coach:2026-10-19")["status"] == "SUCCEEDED"


def test_alte_coach_json_wird_kontrolliert_uebernommen(data):
    job = jobs.create("Coach", "bg-alt", "hintergrund")
    jobs.set_result(job, "Altes Briefing")
    jobs.finish(job, jobs.COMPLETED)
    (data / "coach.json").write_text(json.dumps({"datum": "2026-10-19", "job": job["id"], "erwaehnt": True}))
    assert scheduler.migrate_coach_json() is True
    run = scheduler.get_run("coach:2026-10-19")
    assert run["status"] == "SUCCEEDED" and run["mentioned"] == 1 and run["result"] == "Altes Briefing"
    assert not (data / "coach.json").exists() and (data / "coach.json.migriert").exists()
    assert scheduler.tick(MONDAY_0800, wait=True) == []  # heute schon erledigt


def test_nur_eine_schleife_und_abläufe_standardmaessig_aus(data, background):
    assert scheduler.start_loop() is True
    assert scheduler.start_loop() is False  # keine zweite Schleife
    names = [t.name for t in threading.enumerate()]
    assert names.count("jarvis-scheduler") == 1 and "jarvis-coach" not in names
    states = {k: scheduler.workflow_settings(w)["aktiv"] for k, w in scheduler.REGISTRY.items()}
    assert states == {"coach": True, "fristwarnung": False, "mail_zu_auftrag": False, "lead_followup": False,
                      "termin_vorbereitung": False}
    (data / "ablaeufe.json").write_text(json.dumps({"coach": {"aktiv": False}, "fristwarnung": {"aktiv": True}}))
    assert scheduler.workflow_settings(scheduler.REGISTRY["coach"])["aktiv"] is False
    assert "fristwarnung: Fristwarnung | AN" in scheduler.status_text()


def test_fristwarnung_lokal_ohne_ki(data, background):
    calls, _ = background
    (data / "ablaeufe.json").write_text(json.dumps({"coach": {"aktiv": False}, "fristwarnung": {"aktiv": True}}))
    orders.create({"titel": "Angebot Weber", "frist": "2026-10-17"})
    orders.create({"titel": "Website", "frist": "2026-10-20"})
    orders.create({"titel": "Später", "frist": "2026-12-01"})
    assert scheduler.tick(MONDAY_0800, wait=True) == ["fristwarnung:2026-10-19"]
    result = scheduler.get_run("fristwarnung:2026-10-19")["result"]
    assert "Überfällig: Angebot Weber" in result and "Bald fällig: Website" in result and "Später" not in result
    assert calls == []


def test_ablauf_werkzeugrahmen_wird_im_agenten_erzwungen():
    a = A.Agent(channel="hintergrund", conversation_id="bg-scope")
    a.tool_scope = {"mails_suchen", "auftrag_anlegen"}
    names = {d.get("name") for d in a._tool_definitions()}
    assert names <= {"mails_suchen", "auftrag_anlegen"} and "web_search" not in names
    import types

    blocked = a._execute(types.SimpleNamespace(name="mail_senden", id="x", input={"an": "a@b.de"}))
    assert blocked["is_error"] and "nicht erlaubt" in blocked["content"]


# --- n8n --------------------------------------------------------------------------------------------------

SECRET = "-".join(["unit", "test", "signatur", "geheimnis"])


@pytest.fixture
def flows(data):
    (data / "n8n.json").write_text(json.dumps({"workflows": {
        "lead_import": {"url": "https://n8n.example.de/webhook/a", "beschreibung": "Leads ins CRM", "freigabe": False,
                        "eingabe": {"pflicht": ["quelle"], "felder": {"quelle": "string", "anzahl": "number"}},
                        "status_url": "https://n8n.example.de/api/v1/executions/{execution_id}",
                        "status_header": {"X-N8N-API-KEY": "status-key"}, "signatur_geheimnis": SECRET,
                        "header": {"X-Jarvis-Key": "k-geheim"}},
        "bericht": {"url": "https://n8n.example.de/webhook/b", "freigabe": False, "antwort": "abschluss"},
        "angebot_senden": {"url": "https://n8n.example.de/webhook/c", "beschreibung": "Angebot mailen"},
    }}))
    return data


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self.text = status, json.dumps(body or {})
        self.is_redirect = False


def serve(monkeypatch, *responses):
    calls, queue = [], list(responses)

    def post(self, url, **kw):
        calls.append((url, kw))
        nxt = queue.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    monkeypatch.setattr(n8n.requests.Session, "post", post)
    return calls


def test_n8n_angenommen_ist_nicht_erledigt_und_wird_nicht_doppelt_gesendet(flows, monkeypatch):
    calls = serve(monkeypatch, Resp(202, {"executionId": "981"}))
    out = n8n.n8n_automatik("lead_import", '{"quelle": "Sheet", "anzahl": 3}')
    assert "angenommen – Abschluss NICHT bestätigt" in out and "Ausführung 981" in out
    url, kw = calls[0]
    assert kw["headers"]["Idempotency-Key"].startswith("n8n-")
    assert kw["headers"]["X-Jarvis-Signature"] == n8n.signature(SECRET, kw["data"])
    again = n8n.n8n_automatik("lead_import", '{"anzahl": 3, "quelle": "Sheet"}')  # gleiche Daten, andere Reihenfolge
    assert again.startswith("Nicht erneut gesendet") and len(calls) == 1
    import sqlite3

    raw = b"".join(str(r).encode() for r in sqlite3.connect(flows / "memory.sqlite3").execute("SELECT * FROM n8n_outbox"))
    assert b"k-geheim" not in raw and b"status-key" not in raw  # keine Schlüssel im Journal


def test_n8n_zeitueberschreitung_ist_unklar_und_wird_nicht_wiederholt(flows, monkeypatch):
    calls = serve(monkeypatch, requests.exceptions.ReadTimeout("langsam"))
    with pytest.raises(ToolFailure, match="UNKLAR"):
        n8n.n8n_automatik("lead_import", '{"quelle": "Sheet"}')
    with pytest.raises(ToolFailure, match="Nicht blind wiederholen"):
        n8n.n8n_automatik("lead_import", '{"quelle": "Sheet"}')
    assert len(calls) == 1


def test_n8n_nicht_erreichbar_darf_gefahrlos_erneut(flows, monkeypatch):
    calls = serve(monkeypatch, requests.exceptions.ConnectionError("refused"), Resp(200, {}))
    with pytest.raises(ToolFailure, match="gefahrlos"):
        n8n.n8n_automatik("lead_import", '{"quelle": "Sheet"}')
    assert "angenommen" in n8n.n8n_automatik("lead_import", '{"quelle": "Sheet"}')
    assert len(calls) == 2


def test_n8n_abschluss_vertrag_und_eingabepruefung(flows, monkeypatch):
    serve(monkeypatch, Resp(200, {"status": "success"}), Resp(500, {}))
    assert "Abschluss bestätigt" in n8n.n8n_automatik("bericht", '{"monat": "10"}')
    with pytest.raises(ToolFailure, match="unklar"):
        n8n.n8n_automatik("bericht", '{"monat": "11"}')  # 500: nie als Erfolg
    for bad in ('{"anzahl": 3}', '{"quelle": 5}', '{"quelle": "x", "fremd": 1}'):
        with pytest.raises(ToolFailure):
            n8n.n8n_automatik("lead_import", bad)
    with pytest.raises(ToolFailure, match="braucht eine Freigabe"):
        n8n.n8n_automatik("angebot_senden", "{}")


def test_n8n_status_abgleich_ueber_ausfuehrungs_id(flows, monkeypatch):
    serve(monkeypatch, Resp(202, {"executionId": "981"}))
    out = n8n.n8n_automatik("lead_import", '{"quelle": "Sheet"}')
    action = out.split("Aktion ")[1].split(")")[0]
    seen = {}

    def get(self, url, headers=None, **kw):
        seen.update(url=url, headers=headers)
        return Resp(200, {"finished": True, "status": "success"})

    monkeypatch.setattr(n8n.requests.Session, "get", get)
    assert "ausgeführt und bestätigt" in n8n.n8n_status(action)
    assert seen["url"].endswith("/executions/981") and seen["headers"] == {"X-N8N-API-KEY": "status-key"}


def test_n8n_callback_nur_mit_gueltiger_signatur_und_richtigem_workflow(flows, monkeypatch):
    serve(monkeypatch, Resp(202, {}))
    out = n8n.n8n_automatik("lead_import", '{"quelle": "Sheet"}')
    action = out.split("Aktion ")[1].split(")")[0]
    body = json.dumps({"action_id": action, "workflow": "lead_import", "status": "success", "execution_id": "77"}).encode()
    assert n8n.handle_callback(body, "sha256=falsch")[0] == 403
    wrong = json.dumps({"action_id": action, "workflow": "bericht", "status": "success"}).encode()
    assert n8n.handle_callback(wrong, n8n.signature(SECRET, wrong))[0] == 403
    assert n8n.handle_callback(body, n8n.signature(SECRET, body)) == (200, "CONFIRMED")
    assert "bestätigt" in n8n.n8n_status(action)


def test_n8n_freigabepflichtiger_workflow_nur_nach_ja(flows, monkeypatch):
    calls = serve(monkeypatch, Resp(200, {}))
    tool = approvals.wrap({t.name: t for t in n8n.tools()}["n8n_starten"])
    out = tool.handler(workflow="angebot_senden", daten='{"kunde": "Mueller"}', bestaetigt_vom_nutzer=False)
    assert out.startswith("VORLAGE erstellt") and calls == []
    item = next(a for a in approvals.listing() if a["werkzeug"] == "n8n_starten" and a["status"] == "WAITING_FOR_APPROVAL")
    assert "angenommen" in approvals.approve(item["id"]) and len(calls) == 1
    assert not any(t.name in ("n8n_konfigurieren", "n8n_registrieren") for t in n8n.tools())  # Registry nicht änderbar


def test_prompt_kennt_ehrliche_n8n_semantik_und_ablaeufe():
    p = A.SYSTEM_PROMPT
    assert '"angenommen" (HTTP 200/202) heisst NICHT "erledigt"' in p and "ablaeufe_status" in p
