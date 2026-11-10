"""Einziger Scheduler für wiederkehrende interne Abläufe (Coach-Briefing, Fristwarnung, Mail -> Auftrag, ...).

Grundsätze:
- Genau eine Schleife pro Prozess (start/stop über die Brücke); keine weiteren Briefing-Threads.
- Jeder Lauf hat einen stabilen Schlüssel (z. B. "coach:2026-10-19"). Die Tabelle scheduled_runs erzwingt ihn
  eindeutig, ein Lauf kann also nicht doppelt starten – auch nicht nach Neustart oder mit zwei Prozessen auf
  derselben Datenbank (Lease mit Ablaufzeit, Heartbeat während der Lauf arbeitet).
- Status getrennt und dauerhaft: PLANNED, STARTED, SUCCEEDED, FAILED, UNCLEAR. Ein Fehler (z. B. Provider) wird
  nie als Erfolg gezählt. Interne, rein lesende/entwerfende Läufe dürfen nach Fehler oder Neustart begrenzt erneut
  laufen (max_versuche); danach bleibt der Lauf FAILED (Dead-Letter) und wird angezeigt.
- Abläufe sind standardmäßig AUS, außer dem bisherigen Coach-Briefing. Eingeschaltet wird in
  ~/.jarvis/ablaeufe.json. Jeder Ablauf hat einen festen Werkzeug-Rahmen (scope); Außenwirkung bleibt Vorlage +
  Einzel-Freigabe (Hintergrund-Aufträge können nie freigeben).
- LangGraph ist kein OS-Scheduler: Ein schlafender oder ausgeschalteter Mac führt nichts aus. Nach dem Aufwachen
  holt die Schleife fällige Läufe desselben Tages nach. OS-Autostart: scripts/autostart.py (Opt-in).
"""

from __future__ import annotations

import datetime
import json
import os
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable

from . import config, persist

CHECK_EVERY = 60.0          # Sekunden zwischen Fälligkeitsprüfungen
LEASE_SECONDS = 180.0       # Lease je laufendem Lauf; Heartbeat verlängert
RUN_TIMEOUT = 1200.0        # ein Lauf darf höchstens so lange dauern
RETRY_AFTER = 15 * 60.0     # frühester neuer Versuch nach Fehler
STATUSES = ("PLANNED", "STARTED", "SUCCEEDED", "FAILED", "UNCLEAR")
_ready: set[str] = set()


@dataclass
class Workflow:
    key: str
    title: str
    default_enabled: bool
    # Fälligkeit: liefert für "jetzt" eine Liste stabiler Lauf-Schlüssel samt Auftrag (leer = nichts fällig)
    due: Callable[["Workflow", datetime.datetime, dict], list[tuple[str, dict]]]
    # Ausführung: gibt (status, ergebnis) zurück; "agent"-Abläufe starten einen Hintergrund-Graphlauf
    kind: str = "agent"                                   # "agent" | "lokal"
    scope: tuple[str, ...] = ()                           # erlaubte Werkzeuge im Hintergrund-Auftrag
    max_attempts: int = 2
    prompt: Callable[[dict], str] | None = None
    run_local: Callable[[dict], str] | None = None
    description: str = ""
    defaults: dict = field(default_factory=dict)


REGISTRY: dict[str, Workflow] = {}


def register(wf: Workflow) -> Workflow:
    REGISTRY[wf.key] = wf
    return wf


# --- Einstellungen ------------------------------------------------------------------------------------

def settings() -> dict:
    path = config.data_dir() / "ablaeufe.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def workflow_settings(wf: Workflow) -> dict:
    raw = settings().get(wf.key) or {}
    merged = {**wf.defaults, **(raw if isinstance(raw, dict) else {})}
    enabled = merged.get("aktiv", wf.default_enabled)
    if wf.key == "coach" and str(config.get("coach_aktiv", "true")).lower() in {"false", "0", "nein", "no"}:
        enabled = False  # bisherige Einstellung bleibt gültig
    merged["aktiv"] = bool(enabled)
    return merged


def daily_at(default: str = "07:30", weekends_key: str = "wochenende"):
    def due(wf: Workflow, now: datetime.datetime, cfg: dict) -> list[tuple[str, dict]]:
        if now.weekday() >= 5 and not cfg.get(weekends_key, False):
            return []
        raw = str(cfg.get("uhrzeit") or (config.get("coach_uhrzeit") if wf.key == "coach" else None) or default)
        try:
            hour, minute = (int(x) for x in raw.split(":", 1))
        except ValueError:
            hour, minute = (int(x) for x in default.split(":"))
        if (now.hour, now.minute) < (hour, minute):
            return []
        return [(f"{wf.key}:{now.date().isoformat()}", {"datum": now.date().isoformat()})]
    return due


def every_hours(hours_key: str = "alle_stunden", default: int = 3, start: int = 7, end: int = 20):
    def due(wf: Workflow, now: datetime.datetime, cfg: dict) -> list[tuple[str, dict]]:
        step = max(1, min(12, int(cfg.get(hours_key, default) or default)))
        if not start <= now.hour < end or now.weekday() >= 5 and not cfg.get("wochenende", False):
            return []
        block = (now.hour - start) // step
        return [(f"{wf.key}:{now.date().isoformat()}:{block}", {"datum": now.date().isoformat(), "block": block})]
    return due


# --- Journal ------------------------------------------------------------------------------------------

def _db():
    cm = persist._connect()
    c = cm.__enter__()
    path = str(persist.db_path())
    if path not in _ready:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS scheduled_runs (
              run_key TEXT PRIMARY KEY, workflow TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL,
              planned REAL NOT NULL, started REAL, finished REAL, lease_owner TEXT, lease_until REAL,
              heartbeat REAL, job_id TEXT, graph_run TEXT, error TEXT, result TEXT, payload TEXT, mentioned INTEGER DEFAULT 0);
            CREATE INDEX IF NOT EXISTS scheduled_runs_wf ON scheduled_runs(workflow, planned);
        """)
        _ready.add(path)
    return cm, c


def _rows(sql: str, args=()) -> list[dict]:
    cm, c = _db()
    try:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(sql, args).fetchall()]
    finally:
        cm.__exit__(None, None, None)


def _exec(sql: str, args=()) -> int:
    cm, c = _db()
    try:
        return c.execute(sql, args).rowcount
    finally:
        cm.__exit__(None, None, None)


def get_run(run_key: str) -> dict | None:
    rows = _rows("SELECT * FROM scheduled_runs WHERE run_key=?", (run_key,))
    return rows[0] if rows else None


def recent_runs(limit: int = 30) -> list[dict]:
    return _rows("SELECT * FROM scheduled_runs ORDER BY planned DESC LIMIT ?", (max(1, min(200, limit)),))


def claim(wf: Workflow, run_key: str, payload: dict, owner: str, now: float | None = None) -> bool:
    """Atomar: Lauf anlegen (PLANNED) und Lease übernehmen. False = läuft schon, erledigt, erschöpft oder zu früh."""
    now = now or time.time()
    cm, c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        c.execute("INSERT OR IGNORE INTO scheduled_runs (run_key, workflow, status, attempts, planned, payload) "
                  "VALUES (?,?,?,?,?,?)", (run_key, wf.key, "PLANNED", 0, now, json.dumps(payload, ensure_ascii=False)))
        row = c.execute("SELECT status, attempts, finished, lease_until FROM scheduled_runs WHERE run_key=?",
                        (run_key,)).fetchone()
        status, attempts, finished, lease_until = row
        retry_ok = status == "FAILED" and attempts < wf.max_attempts and (finished or 0) + RETRY_AFTER <= now
        if not (status == "PLANNED" or retry_ok):
            c.execute("ROLLBACK")
            return False
        c.execute("UPDATE scheduled_runs SET status='STARTED', attempts=attempts+1, started=?, lease_owner=?, "
                  "lease_until=?, heartbeat=?, error=NULL WHERE run_key=?",
                  (now, owner, now + LEASE_SECONDS, now, run_key))
        c.execute("COMMIT")
        return True
    except BaseException:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        cm.__exit__(None, None, None)


def heartbeat(run_key: str, owner: str) -> bool:
    now = time.time()
    return _exec("UPDATE scheduled_runs SET heartbeat=?, lease_until=? WHERE run_key=? AND lease_owner=? AND status='STARTED'",
                 (now, now + LEASE_SECONDS, run_key, owner)) == 1


def finish(run_key: str, owner: str, status: str, *, result: str = "", error: str = "", job_id: str | None = None,
           graph_run: str | None = None) -> None:
    assert status in ("SUCCEEDED", "FAILED", "UNCLEAR")
    _exec("UPDATE scheduled_runs SET status=?, finished=?, result=?, error=?, job_id=COALESCE(?, job_id), "
          "graph_run=COALESCE(?, graph_run), lease_owner=NULL, lease_until=NULL WHERE run_key=? AND lease_owner=?",
          (status, time.time(), result[:4000], error[:500], job_id, graph_run, run_key, owner))


def recover(now: float | None = None) -> int:
    """Nach Neustart/Wake: abgelaufene Leases laufender Läufe -> FAILED ("unterbrochen"), damit sie begrenzt neu
    versucht werden können. Interne Abläufe führen nichts nach außen aus (nur Vorlagen), daher ist das sicher."""
    now = now or time.time()
    return _exec("UPDATE scheduled_runs SET status='FAILED', finished=?, error='Unterbrochen (Neustart oder Ruhezustand)', "
                 "lease_owner=NULL, lease_until=NULL WHERE status='STARTED' AND COALESCE(lease_until, 0) < ?", (now, now))


def migrate_coach_json() -> bool:
    """Bisherige coach.json kontrolliert übernehmen (nicht löschen, sondern als .migriert behalten)."""
    path = config.data_dir() / "coach.json"
    if not path.exists():
        return False
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    if old.get("datum"):
        from . import jobs

        job = jobs.get(old.get("job", "")) if old.get("job") else None
        status = {"COMPLETED": "SUCCEEDED", "FAILED": "FAILED", "CANCELLED": "FAILED"}.get((job or {}).get("status"), "UNCLEAR")
        cm, c = _db()
        try:
            c.execute("INSERT OR IGNORE INTO scheduled_runs (run_key, workflow, status, attempts, planned, finished, job_id, "
                      "result, mentioned, payload) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (f"coach:{old['datum']}", "coach", status, 1, time.time(), time.time(), old.get("job"),
                       str((job or {}).get("ergebnis") or "")[:4000], 1 if old.get("erwaehnt") else 0,
                       json.dumps({"datum": old["datum"], "migriert": True})))
        finally:
            cm.__exit__(None, None, None)
    path.rename(path.with_name("coach.json.migriert"))
    return True


# --- Ausführung ---------------------------------------------------------------------------------------

def _run_agent(wf: Workflow, run_key: str, owner: str, payload: dict) -> None:
    from . import jobs, runtime

    job = runtime.start_background(wf.prompt(payload), tools=set(wf.scope), title=f"{wf.title} ({payload.get('datum', '')})")
    _exec("UPDATE scheduled_runs SET job_id=? WHERE run_key=?", (job["id"], run_key))
    deadline = time.time() + RUN_TIMEOUT
    while time.time() < deadline:
        current = jobs.get(job["id"]) or {}
        status = current.get("status")
        if status in (jobs.COMPLETED, jobs.FAILED, jobs.CANCELLED) or (
                status == jobs.WAITING and current.get("ende")):
            text = str(current.get("ergebnis") or "")
            graph_run = current.get("graph_run")
            if status in (jobs.COMPLETED, jobs.WAITING) and text.strip() and not text.startswith("Ich habe hier abgebrochen"):
                finish(run_key, owner, "SUCCEEDED", result=text, job_id=job["id"], graph_run=graph_run)
            else:
                finish(run_key, owner, "FAILED", error=str(current.get("fehler") or status or "leeres Ergebnis"),
                       job_id=job["id"], graph_run=graph_run)
            return
        if not heartbeat(run_key, owner):
            return  # Lease verloren (z. B. anderer Prozess hat übernommen): nichts mehr schreiben
        time.sleep(1.0)
    runtime.cancel_job(job["id"])
    finish(run_key, owner, "UNCLEAR", error="Zeitlimit überschritten", job_id=job["id"])


def execute(wf: Workflow, run_key: str, payload: dict, owner: str) -> None:
    try:
        if wf.kind == "lokal":
            result = wf.run_local(payload)
            finish(run_key, owner, "SUCCEEDED", result=result)
        else:
            _run_agent(wf, run_key, owner, payload)
    except Exception as exc:
        finish(run_key, owner, "FAILED", error=f"{type(exc).__name__}: {exc}")
    finally:
        from . import events

        row = get_run(run_key) or {}
        events.log("ablauf", ablauf=wf.key, lauf=run_key, status=row.get("status"), versuch=row.get("attempts"))


def tick(now: datetime.datetime | None = None, owner: str | None = None, wait: bool = False) -> list[str]:
    """Eine Fälligkeitsprüfung: startet fällige Läufe (jeder in eigenem Thread). Gibt gestartete Schlüssel zurück."""
    now = now or datetime.datetime.now()
    owner = owner or OWNER
    started, threads = [], []
    recover()
    for wf in list(REGISTRY.values()):
        cfg = workflow_settings(wf)
        if not cfg["aktiv"]:
            continue
        if wf.kind == "agent" and not _ready_for_agent(wf):
            continue
        for run_key, payload in wf.due(wf, now, cfg):
            if claim(wf, run_key, {**payload, **{k: v for k, v in cfg.items() if k != "aktiv"}}, owner):
                started.append(run_key)
                t = threading.Thread(target=execute, args=(wf, run_key, payload | {"cfg": cfg}, owner),
                                     name=f"jarvis-ablauf-{run_key}", daemon=True)
                t.start()
                threads.append(t)
    if wait:
        for t in threads:
            t.join(RUN_TIMEOUT)
    return started


def _ready_for_agent(wf: Workflow) -> bool:
    from .tools import google

    needs_google = any(t.startswith(("kalender", "mail", "drive", "aufgabe", "entwurf")) for t in wf.scope)
    return not needs_google or google.is_connected()


OWNER = f"{os.getpid()}-{uuid.uuid4().hex[:6]}"
_stop = threading.Event()
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()


def start_loop() -> bool:
    """Startet die EINZIGE Scheduler-Schleife dieses Prozesses (zweiter Aufruf tut nichts)."""
    global _thread
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return False
        _stop.clear()
        try:
            migrate_coach_json()
        except Exception:
            pass

        def loop():
            while not _stop.wait(5 if not getattr(loop, "ran", False) else CHECK_EVERY):
                loop.ran = True
                try:
                    tick()
                except Exception as exc:
                    from . import events

                    events.log("ablauf_fehler", fehlerart=type(exc).__name__)

        _thread = threading.Thread(target=loop, name="jarvis-scheduler", daemon=True)
        _thread.start()
        return True


def stop_loop() -> None:
    global _thread
    _stop.set()
    with _thread_lock:
        _thread = None


def loop_running() -> bool:
    return _thread is not None and _thread.is_alive()


def pending_notes(today: str | None = None) -> list[str]:
    """Einmalige Hinweise fertiger Briefings von heute für den Zustandsvermerk (danach als erwähnt markiert)."""
    today = today or datetime.date.today().isoformat()
    rows = _rows("SELECT run_key, workflow, result FROM scheduled_runs WHERE status='SUCCEEDED' AND mentioned=0 "
                 "AND run_key LIKE ? ORDER BY finished", (f"%:{today}%",))
    notes = []
    for row in rows:
        wf = REGISTRY.get(row["workflow"])
        if wf is None or not row["result"]:
            continue
        if _exec("UPDATE scheduled_runs SET mentioned=1 WHERE run_key=? AND mentioned=0", (row["run_key"],)) == 1:
            notes.append(f"{wf.title} von heute liegt vor. Erwaehne es zu Beginn in einem Satz und biete an, die "
                         f"Vorschlaege umzusetzen. Kurzfassung: {' '.join(row['result'].split())[:900]}")
    return notes


def status_text() -> str:
    lines = []
    for wf in REGISTRY.values():
        cfg = workflow_settings(wf)
        last = _rows("SELECT run_key, status, error FROM scheduled_runs WHERE workflow=? ORDER BY planned DESC LIMIT 1",
                     (wf.key,))
        tail = f" | letzter Lauf {last[0]['run_key']}: {last[0]['status']}" + (f" ({last[0]['error']})" if last and last[0]['error'] else "") if last else ""
        lines.append(f"{wf.key}: {wf.title} | {'AN' if cfg['aktiv'] else 'aus'}{tail}")
    lines.append("Schleife: " + ("läuft" if loop_running() else "aus") + ". Einschalten in ~/.jarvis/ablaeufe.json.")
    return "\n".join(lines)
