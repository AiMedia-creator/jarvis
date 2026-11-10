"""Jarvis-Laufzeitfähigkeiten: Aktionen, die Jarvis selbst über die App ausführt.

Abgrenzung (bewusst drei verschiedene Dinge):
1. Claude-Code-Skills (z. B. /office-hours unter ~/.claude/skills) geben dem *Programmierassistenten* Anweisungen.
   Jarvis kennt sie nicht und kann sie nicht ausführen – sie werden hier weder kopiert noch geladen.
2. Jarvis-Skills (jarvis/skills/*.md, ~/.jarvis/skills) sind Markdown-*Anleitungen*, die Claude über das Werkzeug
   `lies_skill` liest. Sie führen selbst nichts aus.
3. Fähigkeiten (dieses Modul) sind *ausführbar*: Name, Beschreibung, Eingabeschema, Berechtigung, Ausführung,
   Status und Abbruch. Claude ruft sie als Werkzeug auf (Studio und Echtzeit), das Dashboard sieht ihren Zustand.

Ausführung ist vom Gespräch getrennt: Jeder Lauf ist ein Job (jobs.py, in SQLite gespeichert) und läuft in einem
eigenen Thread-Pool. Ein Barge-in, ein neuer Gesprächszug oder ein Ende der Sprachsitzung bricht ihn nicht ab;
abbrechen kann nur ausdrücklich `cancel()` (Knopf im Command Center oder /api/jobs/cancel).
Wiederaufnahme: Was bei einem Neustart noch lief, markiert jobs.restore() als FAILED – nichts läuft heimlich
weiter oder doppelt. Ein fehlgeschlagener Lauf kann mit denselben Eingaben neu gestartet werden (`retry`).
Berechtigung "extern" (Daten außerhalb von Jarvis ändern) läuft immer über Vorlage + Freigabe (approvals.wrap).
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

BERECHTIGUNGEN = {
    "lesen": "liest nur",
    "lokal": "ändert nur lokale Jarvis-Daten (keine Freigabe nötig)",
    "extern": "ändert Daten außerhalb von Jarvis – erst nach deiner Freigabe",
}


class Abgebrochen(Exception):
    """Der Lauf wurde ausdrücklich abgebrochen."""


@dataclass(frozen=True)
class Faehigkeit:
    name: str
    titel: str
    beschreibung: str
    eingabe: dict[str, Any]
    pflicht: list[str]
    berechtigung: str
    ausfuehren: Callable[[dict, "Lauf"], dict]
    abbrechbar: bool = True
    #: Sekunden, die ein Werkzeugaufruf auf das Ergebnis wartet; danach läuft der Job im Hintergrund weiter.
    warten: float = 15.0

    def definition(self) -> dict:
        return {"name": self.name, "titel": self.titel, "beschreibung": self.beschreibung,
                "eingabe": {"type": "object", "properties": self.eingabe, "required": list(self.pflicht)},
                "berechtigung": self.berechtigung, "berechtigung_text": BERECHTIGUNGEN[self.berechtigung],
                "abbrechbar": self.abbrechbar}


@dataclass
class Lauf:
    """Kontext eines Laufs: Fortschritt melden und Abbruch prüfen."""
    job: dict
    faehigkeit: str
    args: dict
    quelle: str
    abbruch: threading.Event = field(default_factory=threading.Event)
    ergebnis: dict | None = None
    fehler: str | None = None

    def pruefen(self) -> None:
        if self.abbruch.is_set():
            raise Abgebrochen()

    def fortschritt(self, text: str) -> None:
        _publish(self, "fortschritt", text=text[:200])


_registry: dict[str, Faehigkeit] = {}
_laeufe: dict[str, Lauf] = {}
_lock = threading.Lock()
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="jarvis-faehigkeit")


def register(cap: Faehigkeit) -> Faehigkeit:
    if cap.berechtigung not in BERECHTIGUNGEN:
        raise ValueError(f"unbekannte Berechtigung {cap.berechtigung}")
    if cap.berechtigung == "extern" and "bestaetigt_vom_nutzer" not in cap.eingabe:
        raise ValueError("Externe Fähigkeiten brauchen die Freigabe (Feld bestaetigt_vom_nutzer)")
    with _lock:
        _registry[cap.name] = cap
    return cap


def get(name: str) -> Faehigkeit | None:
    _ensure_builtin()
    return _registry.get(name)


def definitions() -> list[dict]:
    _ensure_builtin()
    return [c.definition() for c in _registry.values()]


def _public(lauf: Lauf) -> dict:
    job = lauf.job
    return {"id": job["id"], "faehigkeit": lauf.faehigkeit, "quelle": lauf.quelle, "status": job["status"],
            "start": job["start"], "ende": job["ende"], "eingabe": {k: str(v)[:120] for k, v in lauf.args.items()},
            "ergebnis": lauf.ergebnis, "fehler": lauf.fehler or job.get("fehler"),
            "abbrechbar": bool(_registry.get(lauf.faehigkeit, None) and _registry[lauf.faehigkeit].abbrechbar)
            and job["status"] in ("RUNNING", "PLANNED")}


def _publish(lauf: Lauf, phase: str, **extra) -> None:
    from . import runtime

    runtime.bus.publish({"typ": "faehigkeit", "phase": phase, "lauf": _public(lauf), "job_id": lauf.job["id"],
                         "conversation_id": lauf.job["conversation"], **extra})


def _source() -> str:
    from . import approvals

    ctx = approvals._current() if hasattr(approvals, "_current") else None
    return str((ctx or {}).get("kanal") or "jarvis")[:24]


def start(name: str, args: dict, quelle: str | None = None) -> tuple[Lauf, concurrent.futures.Future]:
    """Startet einen Lauf als Job – unabhängig vom aufrufenden Gesprächszug."""
    from . import jobs
    from .tools import Tool, ToolFailure

    cap = get(name)
    if cap is None:
        raise ToolFailure(f"Unbekannte Fähigkeit {name}")
    if cap.berechtigung == "extern" and not (args or {}).get("bestaetigt_vom_nutzer"):
        raise ToolFailure("Externe Fähigkeit ohne Freigabe: erst Vorlage erstellen und bestätigen lassen.")
    given = {k: v for k, v in dict(args or {}).items() if v not in (None, "")}  # leere Felder = nicht genannt
    clean = Tool(cap.name, cap.beschreibung, cap.eingabe, lambda **_: "", list(cap.pflicht)).validate(given)
    job = jobs.create(f"{cap.titel}: {next(iter(clean.values()), '')}", "faehigkeiten", "faehigkeit")
    step = jobs.add_step(job, cap.name, clean, cap.berechtigung)
    lauf = Lauf(job=job, faehigkeit=cap.name, args=clean, quelle=quelle or _source())
    with _lock:
        _laeufe[job["id"]] = lauf
        if len(_laeufe) > 100:
            for old in sorted(_laeufe.values(), key=lambda x: x.job["start"])[:20]:
                _laeufe.pop(old.job["id"], None)
    _publish(lauf, "gestartet")

    def run() -> dict | None:
        try:
            lauf.pruefen()
            result = cap.ausfuehren(clean, lauf)
            lauf.pruefen()
            lauf.ergebnis = result
            jobs.finish_step(step, jobs.COMPLETED, str(result.get("text", "")))
            jobs.set_result(job, str(result.get("text", "")))
            jobs.finish(job, jobs.COMPLETED)
            _publish(lauf, "fertig")
            return result
        except Abgebrochen:
            jobs.finish_step(step, jobs.CANCELLED)
            jobs.finish(job, jobs.CANCELLED)
            _publish(lauf, "abgebrochen")
            return None
        except Exception as exc:  # Fehler sichtbar machen, nie verschlucken
            lauf.fehler = str(exc)[:300] or type(exc).__name__
            jobs.finish_step(step, jobs.FAILED, error=lauf.fehler)
            jobs.finish(job, jobs.FAILED, lauf.fehler)
            _publish(lauf, "fehler")
            return None

    return lauf, _pool.submit(run)


def run_tool(name: str, args: dict) -> str:
    """Werkzeugaufruf durch Claude: kurz warten, sonst läuft der Job im Hintergrund weiter (Sitzung bleibt frei)."""
    from .tools import ToolFailure

    cap = get(name)
    lauf, future = start(name, args)
    try:
        future.result(timeout=cap.warten if cap else 15)
    except concurrent.futures.TimeoutError:
        return (f"{cap.titel if cap else name} läuft noch als Job {lauf.job['id']}. Der Fortschritt steht im Command Center; "
                "sag dem Nutzer, dass es im Hintergrund weiterläuft.")
    if lauf.job["status"] == "CANCELLED":
        raise ToolFailure("Abgebrochen: Der Nutzer hat diesen Lauf gestoppt.")
    if lauf.fehler:
        raise ToolFailure(lauf.fehler)
    return str((lauf.ergebnis or {}).get("text") or "Erledigt.")


def tool(name: str):
    """Fähigkeit als Werkzeug für Claude (Studio und Echtzeit)."""
    from .tools import Tool

    cap = get(name)
    if cap is None:
        raise KeyError(name)
    return Tool(cap.name, cap.beschreibung, dict(cap.eingabe), lambda **kw: run_tool(cap.name, kw), list(cap.pflicht),
                timeout=cap.warten + 5)


def cancel(job_id: str) -> bool:
    from . import jobs

    with _lock:
        lauf = _laeufe.get(job_id)
    if lauf is None or lauf.job["status"] not in (jobs.RUNNING, jobs.PLANNED):
        return False
    cap = _registry.get(lauf.faehigkeit)
    if cap is not None and not cap.abbrechbar:
        return False
    lauf.abbruch.set()
    jobs.finish(lauf.job, jobs.CANCELLED)
    _publish(lauf, "abgebrochen")
    return True


def retry(job_id: str) -> dict:
    """Wiederaufnahme: fehlgeschlagenen/abgebrochenen Lauf mit denselben Eingaben neu starten."""
    from .tools import ToolFailure

    with _lock:
        lauf = _laeufe.get(job_id)
    if lauf is None:
        raise ToolFailure("Lauf nicht mehr im Speicher (z. B. nach Neustart) – bitte neu beauftragen.")
    if lauf.job["status"] not in ("FAILED", "CANCELLED"):
        raise ToolFailure("Nur fehlgeschlagene oder abgebrochene Läufe können wiederholt werden.")
    new, _ = start(lauf.faehigkeit, lauf.args, lauf.quelle)
    return _public(new)


def recent(limit: int = 20) -> list[dict]:
    with _lock:
        items = sorted(_laeufe.values(), key=lambda x: x.job["start"], reverse=True)[:limit]
    return [_public(x) for x in items]


def is_run(job_id: str) -> bool:
    with _lock:
        return job_id in _laeufe


# --- Eingebaute Fähigkeiten ----------------------------------------------------------------------------------

def _auftrag_erfassen(args: dict, lauf: Lauf) -> dict:
    """Legt den Auftrag in der vorhandenen Auftragsverwaltung (orders.py, SQLite) an – Spalte Eingang."""
    from . import orders

    fields = {k: v for k, v in args.items() if v not in (None, "")}
    fields.setdefault("prioritaet", "normal")
    if not fields.get("quelle"):
        fields["quelle"] = f"jarvis:{lauf.quelle}"
    lauf.pruefen()
    try:
        # request_id = Job-ID: ein wiederholter Aufruf desselben Laufs legt nichts doppelt an.
        order = orders.create(fields, request_id="lauf-" + lauf.job["id"], source="jarvis")
    except orders.OrderError as exc:
        raise ValueError(f"Auftrag nicht angelegt: {exc}") from exc
    order = orders.link(order["id"], job_id=lauf.job["id"], source="faehigkeit")  # Herkunft am Auftrag sichtbar
    return {"text": f"Auftrag {order['id']} angelegt (Eingang, v{order['version']}): {order['titel']}. "
                    "Er steht jetzt unter Aufträge.",
            "auftrag_id": order["id"], "titel": order["titel"], "status": order["status"],
            "prioritaet": order["prioritaet"], "kunde": order.get("kunde", ""), "frist": order.get("frist", "")}


_builtin_done = False


def _ensure_builtin() -> None:
    global _builtin_done
    if _builtin_done:
        return
    _builtin_done = True
    from .tools import string

    register(Faehigkeit(
        name="auftrag_anlegen",
        titel="Auftrag erfassen",
        beschreibung="Erfasst einen Auftrag (z. B. gesprochen: „Leg einen Auftrag an …“) in der Auftragsverwaltung "
                     "(Kanban, Spalte Eingang). Nur lokal, keine Freigabe nötig. Titel kurz und konkret; Kunde, "
                     "Priorität, Frist und Notiz nur, wenn der Nutzer sie nennt – nichts erfinden.",
        eingabe={"titel": string("kurzer Titel"), "kunde": string("Kunde/Projekt (optional)"),
                 "prioritaet": {"type": "string", "enum": ["niedrig", "normal", "hoch", "kritisch"],
                                "description": "Priorität (optional, Standard normal)"},
                 "frist": string("JJJJ-MM-TT (optional)"), "notiz": string("Details (optional)"),
                 "quelle": string("Herkunft, z. B. Mail-id (optional)")},
        pflicht=["titel"],
        berechtigung="lokal",
        ausfuehren=_auftrag_erfassen,
        abbrechbar=True,
        warten=10.0,
    ))
