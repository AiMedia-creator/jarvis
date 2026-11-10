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
import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

BERECHTIGUNGEN = {
    "lesen": "liest nur",
    "lokal": "ändert nur lokale Jarvis-Daten (keine Freigabe nötig)",
    "extern": "ändert Daten außerhalb von Jarvis – erst nach deiner Freigabe",
    # Getrennte Rechte für Code-Arbeit. Keine eingebaute Fähigkeit nutzt sie; jede, die es täte, braucht Freigabe.
    "code_ausfuehren": "führt fremden Code aus – nur nach deiner Freigabe",
    "repo_schreiben": "ändert Dateien in einem Repository – nur auf separatem Branch, nach Freigabe",
    "push": "überträgt Änderungen zu GitHub – nur auf separatem Branch, nach Freigabe",
    "merge": "führt Branches zusammen – nie automatisch, nur nach Freigabe",
    "veroeffentlichen": "macht etwas öffentlich – nur nach Freigabe",
}
FREIGABEPFLICHTIG = {"extern", "code_ausfuehren", "repo_schreiben", "push", "merge", "veroeffentlichen"}


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
    #: Harte Obergrenze eines Laufs in Sekunden; danach wird er abgebrochen und als fehlgeschlagen markiert.
    zeitlimit: float = 300.0
    #: ausfuehrbar (Python-Code in Jarvis) | skill (Markdown-Anleitung als Auftrag) | cloud (Anthropic Agent Skill)
    art: str = "ausfuehrbar"
    #: Wenn gesetzt, ist die Fähigkeit derzeit nicht nutzbar (z. B. fehlende Anmeldung); Text erklärt warum.
    sperre: Callable[[], str] | None = None

    def gesperrt(self) -> str:
        try:
            return (self.sperre() or "") if self.sperre else ""
        except Exception as exc:  # Prüfung selbst darf die Liste nicht sprengen
            return f"Status nicht prüfbar: {type(exc).__name__}"

    def definition(self) -> dict:
        return {"name": self.name, "titel": self.titel, "beschreibung": self.beschreibung,
                "eingabe": {"type": "object", "properties": self.eingabe, "required": list(self.pflicht)},
                "berechtigung": self.berechtigung, "berechtigung_text": BERECHTIGUNGEN[self.berechtigung],
                "abbrechbar": self.abbrechbar, "zeitlimit_s": int(self.zeitlimit), "art": self.art,
                "version": "1", "herkunft": "jarvis (eingebaut)", "aktiviert": not self.gesperrt(),
                "sperrgrund": self.gesperrt()}


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
    fortschritt_text: str = ""

    def pruefen(self) -> None:
        if self.abbruch.is_set():
            raise Abgebrochen()

    def fortschritt(self, text: str) -> None:
        self.fortschritt_text = text[:200]
        _publish(self, "fortschritt", text=self.fortschritt_text)


_registry: dict[str, Faehigkeit] = {}
_laeufe: dict[str, Lauf] = {}
_lock = threading.Lock()
_pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="jarvis-faehigkeit")


def register(cap: Faehigkeit) -> Faehigkeit:
    if cap.berechtigung not in BERECHTIGUNGEN:
        raise ValueError(f"unbekannte Berechtigung {cap.berechtigung}")
    if cap.berechtigung in FREIGABEPFLICHTIG and "bestaetigt_vom_nutzer" not in cap.eingabe:
        raise ValueError("Freigabepflichtige Fähigkeiten brauchen das Feld bestaetigt_vom_nutzer (Vorlage + Freigabe)")
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
    cap = _registry.get(lauf.faehigkeit)
    return {"id": job["id"], "faehigkeit": lauf.faehigkeit, "quelle": lauf.quelle, "status": job["status"],
            "start": job["start"], "ende": job["ende"], "eingabe": {k: str(v)[:120] for k, v in lauf.args.items()},
            "ergebnis": lauf.ergebnis, "fehler": lauf.fehler or job.get("fehler"),
            "fortschritt": lauf.fortschritt_text,
            "schritte": [{"werkzeug": s.get("werkzeug"), "status": s.get("status")} for s in job.get("schritte", [])][-12:],
            "abbrechbar": bool(cap and cap.abbrechbar) and job["status"] in ("RUNNING", "PLANNED"),
            "wiederholbar": job["status"] in ("FAILED", "CANCELLED")}


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
    # Freigabepflichtige Fähigkeiten erreichen start() nur über approvals.wrap (Vorlage + Freigabe, siehe tool()).
    given = {k: v for k, v in dict(args or {}).items() if v not in (None, "")}  # leere Felder = nicht genannt
    clean = Tool(cap.name, cap.beschreibung, cap.eingabe, lambda **_: "", list(cap.pflicht)).validate(given)
    if cap.gesperrt():
        raise ToolFailure(f"{cap.titel} ist derzeit nicht nutzbar: {cap.gesperrt()}")
    first = str(next(iter(clean.values()), "")).strip()
    job = jobs.create(f"{cap.titel}: {first}" if first else cap.titel, "faehigkeiten", "faehigkeit")
    # Wiederaufnahme auch nach einem Neustart: Eingaben liegen mit dem Job in SQLite (persist.save_job).
    job["wiederaufnahme"] = {"faehigkeit": cap.name, "args": {k: str(v)[:2000] if isinstance(v, str) else v
                                                              for k, v in clean.items()}}
    step = jobs.add_step(job, cap.name, clean, cap.berechtigung)
    lauf = Lauf(job=job, faehigkeit=cap.name, args=clean, quelle=quelle or _source())
    with _lock:
        _laeufe[job["id"]] = lauf
        if len(_laeufe) > 100:
            for old in sorted(_laeufe.values(), key=lambda x: x.job["start"])[:20]:
                _laeufe.pop(old.job["id"], None)
    _publish(lauf, "gestartet")
    watchdog = threading.Timer(cap.zeitlimit, lambda: cancel(job["id"], f"Zeitlimit von {int(cap.zeitlimit)} s überschritten"))
    watchdog.daemon = True
    watchdog.start()

    def run() -> dict | None:
        try:
            lauf.pruefen()
            result = cap.ausfuehren(clean, lauf)
            lauf.pruefen()
            lauf.ergebnis = result
            waiting = bool(result.get("wartet_auf_freigabe"))
            jobs.finish_step(step, jobs.WAITING if waiting else jobs.COMPLETED, str(result.get("text", "")))
            jobs.set_result(job, str(result.get("text", "")))
            jobs.finish(job, jobs.COMPLETED)  # bleibt WAITING_FOR_APPROVAL, wenn ein Schritt auf Freigabe wartet
            _publish(lauf, "fertig")
            return result
        except Abgebrochen:
            jobs.finish_step(step, jobs.CANCELLED if job["status"] != jobs.FAILED else jobs.FAILED)
            jobs.finish(job, jobs.CANCELLED)
            _publish(lauf, "abgebrochen" if job["status"] == jobs.CANCELLED else "fehler")
            return None
        except Exception as exc:  # Fehler sichtbar machen, nie verschlucken
            lauf.fehler = str(exc)[:300] or type(exc).__name__
            jobs.finish_step(step, jobs.FAILED, error=lauf.fehler)
            jobs.finish(job, jobs.FAILED, lauf.fehler)
            _publish(lauf, "fehler")
            return None
        finally:
            watchdog.cancel()

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


def tools() -> list:
    """Alle nutzbaren Fähigkeiten als Werkzeuge (gesperrte werden Claude gar nicht erst angeboten)."""
    _ensure_builtin()
    return [tool(name) for name, cap in list(_registry.items()) if not cap.gesperrt() and not name.startswith("test_")]


def tool(name: str):
    """Fähigkeit als Werkzeug für Claude (Studio und Echtzeit)."""
    from .tools import Tool

    cap = get(name)
    if cap is None:
        raise KeyError(name)
    return Tool(cap.name, cap.beschreibung, dict(cap.eingabe), lambda **kw: run_tool(cap.name, kw), list(cap.pflicht),
                timeout=cap.warten + 5)


def cancel(job_id: str, grund: str | None = None) -> bool:
    """Ausdrücklicher Abbruch (oder Zeitlimit, dann mit `grund` als Fehler)."""
    from . import jobs, runtime

    with _lock:
        lauf = _laeufe.get(job_id)
    if lauf is None or lauf.job["status"] not in (jobs.RUNNING, jobs.PLANNED):
        return False
    cap = _registry.get(lauf.faehigkeit)
    if cap is not None and not cap.abbrechbar and grund is None:
        return False
    lauf.abbruch.set()
    agent = runtime._job_agents.get(job_id)  # Skill-Aufträge: auch den Agenten dieses Jobs stoppen
    if agent is not None:
        try:
            agent.cancel()
        except Exception:
            pass
    if grund:
        lauf.fehler = grund
        jobs.finish(lauf.job, jobs.FAILED, grund)
        _publish(lauf, "fehler")
    else:
        jobs.finish(lauf.job, jobs.CANCELLED)
        _publish(lauf, "abgebrochen")
    return True


def retry(job_id: str) -> dict:
    """Wiederaufnahme: fehlgeschlagenen/abgebrochenen Lauf mit denselben Eingaben neu starten."""
    from .tools import ToolFailure

    from . import jobs

    with _lock:
        lauf = _laeufe.get(job_id)
    job = lauf.job if lauf else jobs.get(job_id)  # nach Neustart: aus SQLite wiederhergestellter Job
    saved = (job or {}).get("wiederaufnahme")
    if job is None or not isinstance(saved, dict) or not saved.get("faehigkeit"):
        raise ToolFailure("Für diesen Lauf sind keine Eingaben gespeichert – bitte neu beauftragen.")
    if job["status"] not in ("FAILED", "CANCELLED"):
        raise ToolFailure("Nur fehlgeschlagene oder abgebrochene Läufe können wiederholt werden.")
    new, _ = start(saved["faehigkeit"], dict(saved.get("args") or {}), lauf.quelle if lauf else "wiederaufnahme")
    return _public(new)


def recent(limit: int = 20) -> list[dict]:
    """Letzte Läufe – auch aus früheren Jarvis-Starts (aus den gespeicherten Jobs, ohne Ergebnisdetails)."""
    from . import jobs

    with _lock:
        live = {x.job["id"]: _public(x) for x in _laeufe.values()}
    for job in jobs.recent(50):
        saved = job.get("wiederaufnahme")
        if job["id"] in live or job.get("art") != "faehigkeit" or not isinstance(saved, dict):
            continue
        live[job["id"]] = {"id": job["id"], "faehigkeit": saved.get("faehigkeit"), "quelle": "früherer Start",
                           "status": job["status"], "start": job["start"], "ende": job["ende"],
                           "eingabe": {k: str(v)[:120] for k, v in (saved.get("args") or {}).items()},
                           "ergebnis": {"text": job.get("ergebnis")} if job.get("ergebnis") else None,
                           "fehler": job.get("fehler"), "fortschritt": "", "abbrechbar": False,
                           "schritte": [{"werkzeug": s.get("werkzeug"), "status": s.get("status")} for s in job.get("schritte", [])][-12:],
                           "wiederholbar": job["status"] in ("FAILED", "CANCELLED")}
    return sorted(live.values(), key=lambda x: x["start"], reverse=True)[:limit]


def is_run(job_id: str) -> bool:
    with _lock:
        return job_id in _laeufe


def needs_approval(name: str) -> bool:
    cap = get(name)
    return bool(cap and cap.berechtigung in FREIGABEPFLICHTIG)


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


def _skill_starten(args: dict, lauf: Lauf) -> dict:
    """Führt eine Jarvis-Skill (Anleitung) als eigenen Auftrag aus: eigenes Gespräch, eigener Werkzeug-Rahmen."""
    from . import jobs, runtime, skill_laufzeit, skills
    from .tools import all_tools

    name = args["skill"].strip()
    skill = skills.load_all().get(name)
    if skill is None:
        raise ValueError(f"Unbekannte Skill '{name}'.")
    if not skill_laufzeit.enabled(skill):
        raise ValueError(f"Skill '{name}' ist deaktiviert. Erst im Command Center aktivieren.")
    tools = {t.name: t for t in all_tools(skill_laufzeit.load_active(), None)}
    definition = skill_laufzeit.define(skill, tools)
    if definition.fehlende_werkzeuge and definition.rahmen == "deklariert" and not definition.werkzeuge:
        raise ValueError(f"Keines der benötigten Werkzeuge ist verfügbar: {', '.join(definition.fehlende_werkzeuge)}.")
    scope = None if definition.rahmen == "frei" else set(definition.werkzeuge) | skill_laufzeit.ALWAYS_ALLOWED
    lauf.fortschritt(f"Skill {name} läuft als Auftrag (Rahmen: {definition.rahmen}).")
    conv = "skill-" + lauf.job["id"]
    text = (f"Skill-Auftrag. Lies zuerst die Skill '{name}' mit lies_skill und arbeite sie dann für diesen Auftrag ab. "
            f"Nutze nur die Werkzeuge, die dir zur Verfügung stehen. Auftrag: {args['auftrag']}")
    try:
        job = runtime.run(conv, text, job=lauf.job, kind="skill", channel="hintergrund", tools=scope)
    finally:
        runtime.drop_conversation(conv)
    lauf.pruefen()
    if job["status"] == jobs.CANCELLED:
        raise Abgebrochen()
    if job["status"] == jobs.FAILED:
        raise ValueError(job.get("fehler") or "Skill-Auftrag fehlgeschlagen.")
    waiting = job["status"] == jobs.WAITING or any(s.get("status") == jobs.WAITING for s in job["schritte"])
    return {"text": (job.get("ergebnis") or "Erledigt.")[:4000], "skill": name, "rahmen": definition.rahmen,
            "wartet_auf_freigabe": waiting}


def _github_suchen(args: dict, lauf: Lauf) -> dict:
    from . import github_quelle

    try:
        hits = github_quelle.search(args["begriff"], int(args.get("anzahl") or 5))
    except github_quelle.GitHubFehler as exc:
        raise ValueError(str(exc)) from None
    text = "\n".join(f"{h['repo']} ★{h['sterne']} {h['sprache']} {h['lizenz']} – {h['beschreibung']}" for h in hits)
    return {"text": text or "Keine öffentlichen Repositories gefunden.", "treffer": hits}


def _github_analysieren(args: dict, lauf: Lauf) -> dict:
    from . import github_quelle

    try:
        origin = github_quelle.fetch(args["repo"], args.get("ref") or None, check=lauf.pruefen, progress=lauf.fortschritt)
        lauf.pruefen()
        lauf.fortschritt("Lese README, Struktur, Lizenz und Stack (nichts wird ausgeführt) …")
        summary = github_quelle.summarize(origin)
    except github_quelle.GitHubFehler as exc:
        raise ValueError(str(exc)) from None
    return {"text": github_quelle.text_for_claude(summary), "github": summary}


def _cloud_dokument(args: dict, lauf: Lauf) -> dict:
    from . import cloud_skills

    return cloud_skills.run(args["skill"], args["auftrag"], check=lauf.pruefen, progress=lauf.fortschritt)


def _projekt_speichern(args: dict, lauf: Lauf) -> dict:
    from . import anrufe

    lauf.pruefen()
    felder = {k: v for k, v in args.items() if k not in ("projekt_id",) and v not in (None, "")}
    try:
        projekt = anrufe.projekt_speichern(felder, args.get("projekt_id") or None)
    except anrufe.AnrufError as exc:
        raise ValueError(str(exc)) from exc
    return {"text": f"Projekt {projekt['name']} gespeichert ({projekt['id']}, CRM: {projekt['crm']}).", "projekt": projekt}


def _projekt_liste(args: dict, lauf: Lauf) -> dict:
    from . import anrufe

    ps = anrufe.projekte_liste(nur_aktive=True)
    text = "; ".join(f"{p['name']} ({p['id']}, CRM: {p['crm']})" for p in ps) or "Noch kein Projekt angelegt."
    return {"text": text, "projekte": ps}


def _anruf_erfassen(args: dict, lauf: Lauf) -> dict:
    from . import anrufe

    lauf.pruefen()
    try:
        pid = anrufe.projekt_finden(args.get("projekt"))
        felder = {k: v for k, v in args.items() if k != "projekt" and v not in (None, "")}
        # request_id = Job-ID: ein wiederholter Aufruf desselben Laufs speichert nichts doppelt.
        call = anrufe.anruf_erfassen(felder, pid, request_id="lauf-" + lauf.job["id"])
    except anrufe.AnrufError as exc:
        raise ValueError(f"Anruf nicht gespeichert: {exc}") from exc
    teile = [f"Anruf {call['id']} gespeichert: {anrufe.ERGEBNISSE[call['ergebnis']]}"]
    if call.get("auftrag_id"):
        teile.append(f"Nächster Schritt als Auftrag {call['auftrag_id']} im Kanban (Eingang)")
    if call.get("auftrag_fehler"):
        teile.append("Auftrag NICHT angelegt: " + call["auftrag_fehler"])
    teile.append("CRM-Eintrag ist noch offen – nichts wurde ins CRM geschrieben" if call["crm_status"] == "offen"
                 else f"CRM-Status: {call['crm_status']}")
    return {"text": ". ".join(teile) + ".", "anruf": call}


def _anruf_karte(args: dict, lauf: Lauf) -> dict:
    from . import anrufe

    try:
        karte = anrufe.lead_karte(anrufe.projekt_finden(args.get("projekt")), args.get("name", ""), args.get("firma", ""))
    except anrufe.AnrufError as exc:
        raise ValueError(str(exc)) from exc
    return {"text": json.dumps(karte, ensure_ascii=False), "karte": karte}


def _anruf_kennzahlen(args: dict, lauf: Lauf) -> dict:
    from . import anrufe

    try:
        pid = anrufe.projekt_finden(args["projekt"]) if args.get("projekt") else None
        k = anrufe.kennzahlen(int(args.get("tage") or 7), pid)
    except anrufe.AnrufError as exc:
        raise ValueError(str(exc)) from exc
    return {"text": json.dumps(k, ensure_ascii=False), "kennzahlen": k}


def _anruf_crm_status(args: dict, lauf: Lauf) -> dict:
    from . import anrufe

    try:
        call = anrufe.crm_status_setzen(args["anruf_id"], args["status"])
    except anrufe.AnrufError as exc:
        raise ValueError(str(exc)) from exc
    return {"text": f"CRM-Status von {call['id']}: {call['crm_status']}.", "anruf": call}


def _content_speichern(args: dict, lauf: Lauf) -> dict:
    from . import content

    lauf.pruefen()
    felder = {k: v for k, v in args.items() if k != "content_id" and v not in (None, "")}
    try:
        item = content.speichern(felder, args.get("content_id") or None)
    except content.ContentError as exc:
        raise ValueError(str(exc)) from exc
    return {"text": f"Content {item['id']} gespeichert (Status {item['status']}): {item['titel']}.", "eintrag": item}


def _content_liste(args: dict, lauf: Lauf) -> dict:
    from . import content

    items = content.liste(args.get("status") or None, 30)
    text = "; ".join(f"{i['id']} [{i['status']}] {i['titel']}" for i in items) or "Noch kein Content-Eintrag."
    return {"text": text, "eintraege": items}


def _content_status(args: dict, lauf: Lauf) -> dict:
    from . import content

    try:
        item = content.status_setzen(args["content_id"], args["status"], "jarvis")
    except content.ContentError as exc:
        raise ValueError(str(exc)) from exc
    return {"text": f"Content {item['id']} steht jetzt auf {item['status']}.", "eintrag": item}


def _content_ergebnis(args: dict, lauf: Lauf) -> dict:
    from . import content

    werte = {k: v for k, v in args.items() if k not in ("content_id", "plattform")}
    try:
        item = content.ergebnis_erfassen(args["content_id"], args["plattform"], werte)
    except content.ContentError as exc:
        raise ValueError(str(exc)) from exc
    return {"text": f"Kennzahlen zu {item['id']} ({args['plattform']}) gespeichert – Quelle: vom Nutzer genannt.", "eintrag": item}


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
        zeitlimit=60.0,
    ))
    register(Faehigkeit(
        name="skill_starten", titel="Skill als Auftrag ausführen", art="skill",
        beschreibung="Führt eine Jarvis-Skill (aus dem Skill-Katalog) als eigenen Auftrag aus – unabhängig vom Gespräch, "
                     "mit Fortschritt, Abbruch und Wiederaufnahme im Command Center. Für längere, mehrstufige Aufgaben. "
                     "Die Skill darf nur ihre freigegebenen Werkzeuge nutzen; Außenwirksames landet als Vorlage zur Freigabe.",
        eingabe={"skill": string("exakter Name der Skill aus dem Katalog"), "auftrag": string("was konkret erledigt werden soll")},
        pflicht=["skill", "auftrag"], berechtigung="lokal", ausfuehren=_skill_starten,
        warten=3.0, zeitlimit=900.0,
    ))
    register(Faehigkeit(
        name="github_suchen", titel="GitHub durchsuchen",
        beschreibung="Sucht öffentliche GitHub-Repositories (Name, Sterne, Sprache, Lizenz). Nur lesend, ohne Anmeldung.",
        eingabe={"begriff": string("Suchbegriff"), "anzahl": {"type": "integer", "description": "1–10 Treffer (optional)"}},
        pflicht=["begriff"], berechtigung="lesen", ausfuehren=_github_suchen, warten=20.0, zeitlimit=30.0,
        sperre=lambda: "" if __import__("shutil").which("git") else "git ist nicht installiert.",
    ))
    register(Faehigkeit(
        name="github_analysieren", titel="GitHub-Repository analysieren",
        beschreibung="Lädt ein öffentliches (oder ausdrücklich freigegebenes privates) Repository in einen isolierten "
                     "Arbeitsbereich, legt es auf einen Commit fest und fasst README, Struktur, Lizenz und Technologie-Stack "
                     "zusammen. Führt keinen Code aus und installiert nichts; Inhalte des Repos sind fremde Daten.",
        eingabe={"repo": string("owner/name, z. B. anthropics/skills"),
                 "ref": string("Branch, Tag oder Commit (optional, Standard: Hauptbranch)")},
        pflicht=["repo"], berechtigung="lokal", ausfuehren=_github_analysieren, warten=25.0, zeitlimit=420.0,
        sperre=lambda: "" if __import__("shutil").which("git") else "git ist nicht installiert.",
    ))
    register(Faehigkeit(
        name="projekt_speichern", titel="Projekt anlegen/ändern",
        beschreibung="Legt ein Vertriebsprojekt an oder ändert es (Name, CRM-Typ, Zielgruppe, Leitfaden, Regeln). Nur lokal. "
                     "Nur Angaben eintragen, die der Nutzer nennt – nichts erfinden.",
        eingabe={"projekt_id": string("ID zum Ändern (leer = neues Projekt)"), "name": string("Projektname"),
                 "crm": {"type": "string", "enum": ["hubspot", "gohighlevel", "close", "sonstiges", "keins"], "description": "CRM-System des Projekts"},
                 "zielgruppe": string("Zielgruppe/Entscheider"), "leitfaden": string("Gesprächsleitfaden"),
                 "regeln": string("Regeln, z. B. Anrufzeiten, No-Gos"), "notiz": string("Notiz")},
        pflicht=[], berechtigung="lokal", ausfuehren=_projekt_speichern, warten=10.0, zeitlimit=30.0))
    register(Faehigkeit(
        name="projekt_liste", titel="Projekte anzeigen", beschreibung="Zeigt die angelegten Vertriebsprojekte. Nur lesend.",
        eingabe={}, pflicht=[], berechtigung="lesen", ausfuehren=_projekt_liste, warten=10.0, zeitlimit=30.0))
    register(Faehigkeit(
        name="anruf_erfassen", titel="Anruf-Ergebnis speichern",
        beschreibung="Speichert das Ergebnis eines Anrufs (aus dem Diktat des Nutzers) lokal: Lead, Ergebnis, Notiz, nächster "
                     "Schritt, Frist. Mit nächstem Schritt entsteht ein Auftrag im Kanban. Schreibt NICHT ins CRM und sendet "
                     "nichts – CRM-Notiz und Follow-up-Mail laufen danach über die freigabepflichtigen Werkzeuge. Nur "
                     "gesagte Angaben eintragen; Telefonstatus „unklar“, wenn nicht genannt.",
        eingabe={"projekt": string("Projektname oder ID (leer, wenn es nur ein Projekt gibt)"),
                 "lead_name": string("Name des Gesprächspartners"), "firma": string("Firma"), "rolle": string("Rolle/Titel"),
                 "telefon_status": {"type": "string", "enum": ["geschaeftlich", "privat", "unklar"], "description": "Art der Nummer"},
                 "kontakt_quelle": string("Woher stammen die Kontaktdaten"),
                 "ergebnis": {"type": "string", "enum": ["nicht_erreicht", "falsche_nummer", "kein_interesse", "rueckruf", "interesse", "termin", "abschluss"], "description": "Ergebnis des Anrufs"},
                 "notiz": string("Notiz zum Gespräch"), "naechster_schritt": string("Was passiert als Nächstes"),
                 "frist": string("JJJJ-MM-TT für den nächsten Schritt"), "dauer_s": {"type": "integer", "description": "Gesprächsdauer in Sekunden (optional)"}},
        pflicht=["ergebnis"], berechtigung="lokal", ausfuehren=_anruf_erfassen, warten=10.0, zeitlimit=60.0))
    register(Faehigkeit(
        name="anruf_karte", titel="Mini-Karte vor dem Anruf",
        beschreibung="Karte vor einem Anruf: Projekt-Leitfaden, bisherige Anrufe zum Lead, offene Follow-ups, Hinweise zur "
                     "Telefonnummer. Nur lokale Daten; sagt, ob noch eine CRM-Abfrage nötig ist. Nur lesend.",
        eingabe={"projekt": string("Projektname oder ID"), "name": string("Name des Leads"), "firma": string("Firma des Leads")},
        pflicht=[], berechtigung="lesen", ausfuehren=_anruf_karte, warten=10.0, zeitlimit=30.0))
    register(Faehigkeit(
        name="anruf_kennzahlen", titel="Anruf-Kennzahlen",
        beschreibung="Gemessene Zahlen aus den gespeicherten Anrufen (Gespräche, Ergebnisse, Termine pro 100, offene "
                     "CRM-Einträge, fällige Follow-ups). Nennt Quelle und Stand; fehlende Daten bleiben leer. Nur lesend.",
        eingabe={"tage": {"type": "integer", "description": "Zeitraum in Tagen (Standard 7)"}, "projekt": string("Projekt (optional)")},
        pflicht=[], berechtigung="lesen", ausfuehren=_anruf_kennzahlen, warten=10.0, zeitlimit=30.0))
    register(Faehigkeit(
        name="anruf_crm_status", titel="CRM-Status eines Anrufs setzen",
        beschreibung="Hält fest, wie weit der CRM-Eintrag zu einem gespeicherten Anruf ist (offen, vorschau, geschrieben, "
                     "nicht_noetig). Ändert nur Jarvis-Daten, nicht das CRM.",
        eingabe={"anruf_id": string("ID des Anrufs, z. B. C-1a2b3c4d5e"),
                 "status": {"type": "string", "enum": ["offen", "vorschau", "geschrieben", "nicht_noetig"], "description": "CRM-Status"}},
        pflicht=["anruf_id", "status"], berechtigung="lokal", ausfuehren=_anruf_crm_status, warten=10.0, zeitlimit=30.0))
    register(Faehigkeit(
        name="content_speichern", titel="Content-Eintrag speichern",
        beschreibung="Legt einen Content-Eintrag an oder ergänzt ihn (Titel, Thema, Ziel, Zielgruppe, Plattformen, Hooks mit "
                     "Bewertung 1–5 für klarheit/relevanz/neugier/belegbarkeit, Skript, Quellen, KI-Hinweis). Nur lokal. "
                     "Keine erfundenen Zahlen oder Fakten; Aussagen mit Quelle belegen. Veröffentlicht nichts.",
        eingabe={"content_id": string("ID zum Ändern (leer = neuer Eintrag)"), "titel": string("Titel"), "thema": string("Thema"),
                 "ziel": string("Ziel des Videos"), "zielgruppe": string("Zielgruppe"),
                 "plattformen": string("kommagetrennt: tiktok, instagram, youtube_shorts, youtube"),
                 "hooks": {"type": "array", "items": {"type": "object"}, "description": "Hooks: [{text, klarheit, relevanz, neugier, belegbarkeit, belegt_durch}]"},
                 "skript": string("Skript"), "briefing": string("Briefing/Recherche"), "quellen": string("Quellen, eine je Zeile"),
                 "ki_hinweis": string("Hinweis zu KI-generierten Teilen/Kennzeichnung"), "notiz": string("Notiz")},
        pflicht=[], berechtigung="lokal", ausfuehren=_content_speichern, warten=10.0, zeitlimit=60.0))
    register(Faehigkeit(
        name="content_liste", titel="Content anzeigen", beschreibung="Zeigt die Content-Einträge (optional nach Status). Nur lesend.",
        eingabe={"status": {"type": "string", "enum": ["idee", "recherche", "hooks", "skript", "produktion", "review", "freigegeben", "geplant", "veroeffentlicht", "auswertung"], "description": "Filter (optional)"}},
        pflicht=[], berechtigung="lesen", ausfuehren=_content_liste, warten=10.0, zeitlimit=30.0))
    register(Faehigkeit(
        name="content_status_setzen", titel="Content-Status setzen (bis Review)",
        beschreibung="Setzt den Status eines Content-Eintrags höchstens bis „review“. Ab „freigegeben“ entscheidet nur der Nutzer im Dashboard.",
        eingabe={"content_id": string("ID, z. B. K-1a2b3c4d"),
                 "status": {"type": "string", "enum": ["idee", "recherche", "hooks", "skript", "produktion", "review"], "description": "Neuer Status"}},
        pflicht=["content_id", "status"], berechtigung="lokal", ausfuehren=_content_status, warten=10.0, zeitlimit=30.0))
    register(Faehigkeit(
        name="content_ergebnis_erfassen", titel="Content-Kennzahlen erfassen",
        beschreibung="Speichert Kennzahlen, die der Nutzer nennt (Wiedergabezeit, Abschlussrate, Views, Klicks, Kommentare, Follower, "
                     "Leads, Umsatz) zu einem Beitrag. Nur genannte Werte, nichts schätzen oder erfinden.",
        eingabe={"content_id": string("ID"), "plattform": {"type": "string", "enum": ["tiktok", "instagram", "youtube_shorts", "youtube"], "description": "Plattform"},
                 "wiedergabezeit_s": {"type": "integer", "description": "Ø Wiedergabezeit in Sekunden"}, "abschlussrate_pct": {"type": "integer", "description": "Abschlussrate in %"},
                 "views": {"type": "integer", "description": "Aufrufe"}, "klicks": {"type": "integer", "description": "Klicks"},
                 "kommentare": {"type": "integer", "description": "Kommentare"}, "follower": {"type": "integer", "description": "neue Follower"},
                 "leads": {"type": "integer", "description": "Leads"}, "umsatz_eur": {"type": "integer", "description": "Umsatz in Euro"}},
        pflicht=["content_id", "plattform"], berechtigung="lokal", ausfuehren=_content_ergebnis, warten=10.0, zeitlimit=30.0))
    from . import cloud_skills

    register(Faehigkeit(
        name="cloud_dokument", titel="Dokument mit Anthropic-Skill erzeugen", art="cloud",
        beschreibung="Erzeugt eine Datei (Word, PowerPoint, Excel, PDF) mit einer Anthropic Agent Skill in Anthropics "
                     "Container – ohne Internet und ohne Zugriff auf Jarvis-Werkzeuge; alle Inhalte müssen im Auftrag stehen.",
        eingabe={"skill": {"type": "string", "enum": sorted(cloud_skills.ANTHROPIC_SKILLS), "description": "docx, pptx, xlsx oder pdf"},
                 "auftrag": string("vollständiger Inhalt und Aufbau der Datei")},
        pflicht=["skill", "auftrag"], berechtigung="lokal", ausfuehren=_cloud_dokument, warten=5.0, zeitlimit=600.0,
        sperre=cloud_skills.lock_reason,
    ))
