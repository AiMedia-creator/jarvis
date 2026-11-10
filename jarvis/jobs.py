"""Agenten-Jobs: jeder Auftrag ist ein Job mit Schritten (stepId, Status, Zeiten, Fehler, Berechtigung).

Status: PLANNED, RUNNING, WAITING_FOR_APPROVAL, COMPLETED, FAILED, CANCELLED.
Jede Aenderung wird sofort in SQLite gespeichert (persist.py); restore() stellt den Stand nach einem Neustart her.
"""

from __future__ import annotations

import threading
import time
import uuid

PLANNED, RUNNING, WAITING, COMPLETED, FAILED, CANCELLED = (
    "PLANNED", "RUNNING", "WAITING_FOR_APPROVAL", "COMPLETED", "FAILED", "CANCELLED")

_lock = threading.Lock()
_jobs: dict[str, dict] = {}


RESTART_REASON = "Neustart: Jarvis wurde während der Ausführung beendet."


def _persist(*items: dict) -> None:
    from . import persist

    for job in items:
        with _lock:
            snapshot = dict(job, schritte=[dict(st) for st in job["schritte"]])
        persist.save_job(snapshot)


def _short(v, n=160):
    s = str(v)
    return s if len(s) <= n else s[:n] + "…"


def create(title: str, conversation_id: str = "haupt", kind: str = "vordergrund") -> dict:
    job = {"id": uuid.uuid4().hex[:10], "conversation": conversation_id, "art": kind, "titel": _short(title, 120),
           "status": RUNNING, "schritte": [], "start": time.time(), "ende": None, "fehler": None, "ergebnis": None}
    with _lock:
        _jobs[job["id"]] = job
        if len(_jobs) > 50:
            for old in sorted(_jobs.values(), key=lambda j: j["start"])[:10]:
                _jobs.pop(old["id"], None)
    _persist(job)
    return job


def add_step(job: dict, tool: str, args: dict, permission: str) -> dict:
    step = {"id": uuid.uuid4().hex[:8], "job": job["id"], "werkzeug": tool, "status": RUNNING,
            "berechtigung": permission,
            "eingabe": {k: _short(v, 80) for k, v in (args or {}).items() if k != "bestaetigt_vom_nutzer"},
            "ergebnis": None, "fehler": None, "start": time.time(), "ende": None}
    with _lock:
        job["schritte"].append(step)
    _persist(job)
    return step


def finish_step(step: dict, status: str, result: str = "", error: str | None = None) -> None:
    with _lock:
        step["status"] = status
        step["ergebnis"] = _short(result, 200) if result else None
        step["fehler"] = _short(error, 200) if error else None
        step["ende"] = time.time()
        job = _jobs.get(step["job"])
    if job is not None:
        _persist(job)


def finish(job: dict, status: str, error: str | None = None) -> None:
    with _lock:
        if job["status"] in (COMPLETED, FAILED, CANCELLED):
            return
        if status == COMPLETED and any(s["status"] == WAITING for s in job["schritte"]):
            status = WAITING  # fertig bis auf Freigabe
        job["status"] = status
        job["fehler"] = _short(error, 300) if error else None
        job["ende"] = time.time()
        for s in job["schritte"]:
            if s["status"] in (RUNNING, PLANNED):
                s["status"] = CANCELLED if status == CANCELLED else FAILED
                s["ende"] = time.time()
    _persist(job)


def get(job_id: str) -> dict | None:
    return _jobs.get(job_id)


def set_result(job: dict, text: str) -> None:
    with _lock:
        job["ergebnis"] = text[:4000] if text else None
    _persist(job)


def resolve_approval(approval_id: str, ok: bool) -> None:
    """Freigabe erteilt/abgelehnt: zugehoerigen Schritt und ggf. den Job abschliessen."""
    touched = []
    with _lock:
        for job in _jobs.values():
            for st in job["schritte"]:
                if st.get("freigabe_id") == approval_id and st["status"] == WAITING:
                    st["status"] = COMPLETED if ok else CANCELLED
                    st["ende"] = time.time()
                    touched.append(job)
                    if job["status"] == WAITING and not any(s["status"] == WAITING for s in job["schritte"]):
                        job["status"] = COMPLETED
                        job["ende"] = time.time()
    _persist(*touched)


def recent(limit: int = 15) -> list[dict]:
    with _lock:
        return [dict(j, schritte=[dict(s) for s in j["schritte"]])
                for j in sorted(_jobs.values(), key=lambda j: j["start"], reverse=True)[:limit]]


def cancel_running(conversation_id: str | None = None) -> list[str]:
    ids = []
    with _lock:
        running = [j for j in _jobs.values() if j["status"] == RUNNING
                   and (conversation_id is None or j["conversation"] == conversation_id)]
    for j in running:
        finish(j, CANCELLED)
        ids.append(j["id"])
    return ids


def restore(saved: list[dict]) -> int:
    """Gespeicherte Jobs laden. Was beim Neustart noch lief, wird FAILED – es laeuft nicht von selbst weiter."""
    interrupted = []
    with _lock:
        for job in saved:
            if not isinstance(job, dict) or "id" not in job or job["id"] in _jobs:
                continue
            job.setdefault("schritte", [])
            if job.get("status") in (RUNNING, PLANNED):
                job["status"], job["fehler"], job["ende"] = FAILED, RESTART_REASON, time.time()
                interrupted.append(job)
            for st in job["schritte"]:
                if st.get("status") in (RUNNING, PLANNED):
                    st["status"], st["fehler"], st["ende"] = FAILED, "Neustart", time.time()
                    if job not in interrupted:
                        interrupted.append(job)
            _jobs[job["id"]] = job
        while len(_jobs) > 50:
            _jobs.pop(min(_jobs.values(), key=lambda j: j["start"])["id"], None)
    _persist(*[j for j in interrupted if j["id"] in _jobs])
    return len(saved)
