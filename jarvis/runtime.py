"""Laufzeit: getrennte Gespraeche, Jobs, Ereignis-Bus und Hintergrundauftraege.

- Jede conversation_id hat einen EIGENEN Agenten (eigener Verlauf, eigene Sperre, eigener Abbruch).
- Jeder Auftrag ist ein Job; jedes Ereignis traegt job_id und conversation_id.
- Hintergrundjobs laufen in eigenen Threads und eigenen Gespraechen; sie stoeren die Sprachunterhaltung nicht.
- Der Ereignis-Bus (Long-Polling ueber /api/events) liefert Status, Schritte und Ergebnisse aller Jobs.
"""

from __future__ import annotations

import inspect
import threading
import uuid
from collections import deque
from typing import Callable

from . import jobs
from .tools import Tool, string

MAIN = "haupt"


class EventBus:
    def __init__(self, keep: int = 3000):
        self._events: deque = deque(maxlen=keep)
        self._seq = 0
        self._cond = threading.Condition()

    def publish(self, event: dict) -> dict:
        with self._cond:
            self._seq += 1
            event = dict(event, seq=self._seq)
            self._events.append(event)
            self._cond.notify_all()
            return event

    def since(self, after: int, timeout: float = 0.0) -> tuple[list[dict], int]:
        with self._cond:
            if timeout > 0 and self._seq <= after:
                self._cond.wait_for(lambda: self._seq > after, timeout=timeout)
            return [e for e in self._events if e["seq"] > after], self._seq


bus = EventBus()
_lock = threading.Lock()
_conversations: dict[str, object] = {}
_job_agents: dict[str, object] = {}


def _default_factory(channel: str, conversation_id: str):
    from . import config
    from .agent import Agent

    return Agent(channel=channel, model=config.get("model") or "sonnet", conversation_id=conversation_id)


_factory: Callable = _default_factory


def set_factory(fn: Callable) -> None:
    global _factory
    _factory = fn


def conversation(conversation_id: str = MAIN, channel: str = "web"):
    if conversation_id == MAIN:
        return _factory(channel, conversation_id)  # die Hauptunterhaltung verwaltet die Oberflaeche selbst
    with _lock:
        ag = _conversations.get(conversation_id)
        if ag is None:
            ag = _factory(channel, conversation_id)
            _conversations[conversation_id] = ag
        return ag


def drop_conversation(conversation_id: str) -> None:
    with _lock:
        if conversation_id != MAIN:
            _conversations.pop(conversation_id, None)


def run(conversation_id: str, text: str, sink: Callable[[dict], None] | None = None, job: dict | None = None,
        kind: str = "vordergrund", channel: str = "web", tools: set[str] | None = None) -> dict:
    """Fuehrt einen Auftrag in SEINEM Gespraech aus (blockiert den aufrufenden Thread).
    tools: fester Werkzeug-Rahmen (z. B. fuer geplante Ablaeufe); None = alle Werkzeuge des Agenten."""
    from .agent import Cancelled

    agent = conversation(conversation_id, channel)
    if tools is not None:
        agent.tool_scope = set(tools)
    job = job or jobs.create(text, conversation_id, kind)
    ids = {"job_id": job["id"], "conversation_id": conversation_id}
    _job_agents[job["id"]] = agent

    def emit(event: dict) -> None:
        event = dict(event, **ids)
        if event["typ"] != "token" or kind != "vordergrund":  # Vordergrund-Tokens gehen nur in den eigenen Stream
            bus.publish(event)
        if sink:
            sink(event)

    emit({"typ": "job", "job": _public(job)})
    kwargs = dict(on_sentence=lambda s: emit({"typ": "satz", "text": s}),
                  on_tool=lambda n: emit({"typ": "werkzeug", "name": n}))
    if "on_token" in inspect.signature(agent.ask).parameters:
        kwargs.update(on_token=lambda t: emit({"typ": "token", "text": t}),
                      on_step=lambda st: emit({"typ": "schritt", "schritt": st}), job=job)
    try:
        answer = agent.ask(text, **kwargs)
        jobs.set_result(job, answer or "")
        jobs.finish(job, jobs.COMPLETED)
        emit({"typ": "fertig", "job": _public(job)})
    except Cancelled:
        jobs.finish(job, jobs.CANCELLED)
        emit({"typ": "abgebrochen", "job": _public(job)})
    except Exception as exc:
        import traceback

        traceback.print_exc()
        jobs.finish(job, jobs.FAILED, f"{type(exc).__name__}: {exc}")
        emit({"typ": "fehler", "text": f"{type(exc).__name__}: {exc}"[:500], "job": _public(job)})
    finally:
        _job_agents.pop(job["id"], None)
    return job


def start_background(text: str, tools: set[str] | None = None, title: str | None = None) -> dict:
    """Startet einen Hintergrundjob in einem eigenen Gespraech und Thread; kehrt sofort zurueck."""
    conv = "bg-" + uuid.uuid4().hex[:8]
    job = jobs.create(title or text, conv, "hintergrund")

    def work():
        try:
            run(conv, text, job=job, kind="hintergrund", channel="hintergrund", tools=tools)
        finally:
            drop_conversation(conv)

    threading.Thread(target=work, name=f"jarvis-job-{job['id']}", daemon=True).start()
    return _public(job)


def cancel_job(job_id: str) -> bool:
    job = jobs.get(job_id)
    agent = _job_agents.get(job_id)
    current = getattr(agent, "_job", None) if agent is not None else None
    if agent is not None and (current is None or current.get("id") == job_id):
        agent.cancel()  # bricht nur den Agenten DIESES Jobs ab
    if job is not None and job["status"] in (jobs.RUNNING, jobs.WAITING, jobs.PLANNED):
        jobs.finish(job, jobs.CANCELLED)
        bus.publish({"typ": "abgebrochen", "job_id": job_id, "conversation_id": job["conversation"], "job": _public(job)})
        return True
    return False


def cancel_conversation(conversation_id: str = MAIN) -> list[str]:
    if conversation_id == MAIN:
        agent = _factory("web", MAIN)
    else:
        with _lock:
            agent = _conversations.get(conversation_id)
    if agent is not None:
        agent.cancel()
    return jobs.cancel_running(conversation_id)


def _public(job: dict) -> dict:
    return {k: (list(map(dict, v)) if k == "schritte" else v) for k, v in job.items()}


def background_tool() -> Tool:
    def hintergrund_auftrag(auftrag: str) -> str:
        job = start_background(auftrag)
        return (f"Hintergrundjob {job['id']} gestartet. Er laeuft unabhaengig weiter; das Ergebnis erscheint in der "
                "Job-Liste. Sag dem Nutzer kurz Bescheid und mach mit dem Gespraech weiter.")

    return Tool("hintergrund_auftrag",
                "Startet einen laengeren Auftrag als HINTERGRUNDJOB (z. B. Recherche, mehrere Kontakte pruefen, "
                "Berichte vorbereiten), wenn der Nutzer das moechte oder die Aufgabe laenger dauert. Kehrt sofort zurueck.",
                {"auftrag": string("vollstaendige Beschreibung des Auftrags")}, hintergrund_auftrag, ["auftrag"])
