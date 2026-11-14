"""Vorlagen und Freigaben fuer alle Aktionen nach aussen (Mail, Termin, HubSpot-Eintrag).

Ablauf:
1. Jarvis ruft ein schreibendes Werkzeug mit bestaetigt_vom_nutzer=false auf -> es entsteht eine VORLAGE
   (Status WAITING_FOR_APPROVAL), die in der Oberflaeche als Karte erscheint.
2. Der Nutzer sagt "Ja" (Jarvis ruft dasselbe Werkzeug mit identischen Angaben und true auf) oder klickt "Senden".
3. Ausgefuehrt wird nur, wenn es genau diese Vorlage gibt und sie noch offen ist (kein Senden ohne Vorschau,
   kein doppeltes Senden). Alles landet im Audit-Log.

Serverseitige Regeln (unabhaengig vom Prompt):
- Ein "Ja" per Stimme/Chat zaehlt nur in einem SPAETEREN Nutzerzug als dem, in dem die Vorlage entstand.
  Das Modell kann eine Vorlage also nicht im selben Zug selbst freigeben (z. B. nach Prompt Injection aus einer Mail).
- Hintergrund-Auftraege (niemand hoert zu) koennen nie freigeben, nur Vorlagen erstellen.
- Mit JARVIS_FREIGABE_NUR_KLICK=true gilt ausschliesslich der Klick auf "Senden".
- Ausgefuehrte Aktionen werden dauerhaft protokolliert (aktionen.jsonl, nur Hash der Angaben); eine identische
  neue Vorlage traegt einen Duplikat-Hinweis, auch nach einem Neustart.
- Vorlagen werden in SQLite gespeichert (persist.py) und ueberleben einen Neustart. Was beim Neustart gerade
  ausgefuehrt wurde (RUNNING), wird nie automatisch wiederholt, sondern als UNKLAR markiert.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import threading
import time
import uuid

from . import audit, config
from .tools import Tool, ToolFailure

TTL = 30 * 60  # Vorlagen verfallen nach 30 Minuten

ACTIONS = {
    "mail_senden": ("E-Mail senden", "Gmail"),
    "termin_erstellen": ("Termin anlegen", "Google Kalender"),
    "hubspot_notiz_erstellen": ("Notiz speichern", "HubSpot"),
    "hubspot_aufgabe_erstellen": ("Aufgabe erstellen", "HubSpot"),
    "hubspot_mail_protokollieren": ("E-Mail protokollieren", "HubSpot"),
    "hubspot_mail_senden": ("E-Mail senden und protokollieren", "Gmail + HubSpot"),
    "drive_ordnen": ("Drive umbauen", "Google Drive"),
    "n8n_starten": ("Workflow starten", "n8n"),
    "browser_recherche": ("Browser-Recherche starten (nur lesen)", "Browser Use · lokal in der Sandbox"),
    "browser_interaktion": ("Browser-Interaktion starten (klicken und eingeben)", "Browser Use · lokal in der Sandbox"),
    "browser_absenden": ("Im Browser absenden", "Website laut Karte"),
}


def _drive_preview(args: dict) -> list[dict]:
    from .tools.drive_ordnung import preview

    return preview(args)


# Werkzeuge mit eigener Vorschau (z. B. echte Dateinamen statt ids). Ohne Erfolg: Standardzeilen.
def _browser_agent_preview(args: dict) -> list[dict]:
    from .browser_agent import vorlage_zeilen

    return vorlage_zeilen(args)  # prüft Aufgabe und Domains: ungültige Anfragen erzeugen gar keine Vorlage


def _browser_interaktion_preview(args: dict) -> list[dict]:
    from .browser_agent import vorlage_zeilen

    return vorlage_zeilen(args, "interaktion")


def _browser_absenden_preview(args: dict) -> list[dict]:
    from .browser_agent import absenden_zeilen

    return absenden_zeilen(args)  # unbekannte oder verfallene Aktion: gar keine Vorlage


PREVIEWS = {"drive_ordnen": _drive_preview, "browser_recherche": _browser_agent_preview,
            "browser_interaktion": _browser_interaktion_preview, "browser_absenden": _browser_absenden_preview}
FIELDS = [("an", "An"), ("betreff", "Betreff"), ("titel", "Titel"), ("beginn", "Beginn"), ("ende", "Ende"),
          ("faellig", "Fällig"), ("prioritaet", "Priorität"), ("ort", "Ort"), ("richtung", "Richtung"),
          ("kontakt_id", "HubSpot-Kontakt"), ("deal_id", "HubSpot-Deal"), ("antwort_auf", "Antwort auf Mail"),
          ("text", "Text"), ("beschreibung", "Beschreibung"), ("workflow", "Workflow"), ("daten", "Daten")]

_lock = threading.Lock()
_items: dict[str, dict] = {}
_listeners: list = []  # Funktionen(text), z. B. Hinweis an den Text-Agenten nach einer Klick-Freigabe
_resolved: list = []   # Funktionen(freigabe_id, status) nach Endstatus, z. B. Graph-Abgleich (graph.py)
_turns: dict[str, int] = {}  # Nutzerzug-Zaehler je Quelle (conversation_id bzw. "echtzeit")
_ctx = threading.local()      # Aufrufkontext des laufenden Werkzeugs: Quelle, Zug, Kanal
_handlers: dict = {}          # Werkzeugname -> Original-Handler (fuer nach einem Neustart geladene Vorlagen)
UNCLEAR = "UNCLEAR"
UNCLEAR_TEXT = ("Jarvis wurde während der Ausführung neu gestartet. Nicht erneut ausgeführt – bitte im Zielsystem "
                "prüfen, ob die Aktion angekommen ist.")


def _save(*items: dict) -> None:
    from . import persist

    for a in items:
        with _lock:
            snapshot = {k: (dict(v) if isinstance(v, dict) else v) for k, v in a.items()}
        persist.save_approval(snapshot)
DUPLICATE_WINDOW = 7 * 24 * 3600


def next_turn(source: str) -> int:
    """Neue Nutzeraeusserung in dieser Quelle: Zaehler erhoehen und zurueckgeben."""
    with _lock:
        _turns[source] = _turns.get(source, 0) + 1
        return _turns[source]


def current_turn(source: str) -> int:
    with _lock:
        return _turns.get(source, 0)


@contextlib.contextmanager
def context(source: str, turn: int, channel: str = ""):
    """Bindet Werkzeugaufrufe dieses Threads an Quelle + Nutzerzug (fuer die Freigabepruefung)."""
    previous = getattr(_ctx, "value", None)
    _ctx.value = {"quelle": source, "zug": turn, "kanal": channel}
    try:
        yield
    finally:
        _ctx.value = previous


def _current() -> dict | None:
    return getattr(_ctx, "value", None)


def click_only() -> bool:
    return str(config.get("freigabe_nur_klick", "false") or "false").lower() in {"true", "1", "yes", "ja"}


def _fingerprint(tool: str, key: str) -> str:
    return hashlib.sha256(f"{tool}\n{key}".encode()).hexdigest()[:32]


def _actions_file():
    return config.data_dir() / "aktionen.jsonl"


def _record_executed(a: dict) -> None:
    try:
        path = _actions_file()
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": round(time.time(), 3), "werkzeug": a["werkzeug"],
                                "hash": _fingerprint(a["werkzeug"], a["schluessel"]), "id": a["id"]}) + "\n")
        path.chmod(0o600)
    except OSError:
        pass


def previously_executed(tool: str, key: str) -> float | None:
    """Zeitpunkt, an dem genau diese Aktion zuletzt ausgefuehrt wurde (innerhalb von 7 Tagen), sonst None."""
    fp, last = _fingerprint(tool, key), None
    try:
        with open(_actions_file(), encoding="utf-8") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("hash") == fp and time.time() - float(row.get("ts", 0)) < DUPLICATE_WINDOW:
                    last = float(row["ts"])
    except OSError:
        return None
    return last


def _normalize(args: dict) -> str:
    clean = {k: (v.strip() if isinstance(v, str) else v) for k, v in args.items()
             if k != "bestaetigt_vom_nutzer" and v not in (None, "")}
    return json.dumps(clean, sort_keys=True, ensure_ascii=False)


def _preview(tool: str, args: dict) -> dict:
    title, target = ACTIONS.get(tool, (tool, ""))
    rows = [{"feld": label, "wert": str(args[key])} for key, label in FIELDS if args.get(key) not in (None, "")]
    if tool in PREVIEWS:
        try:
            rows = PREVIEWS[tool](args)
        except ToolFailure:
            raise  # ungueltiger Plan: gar keine Vorlage
        except Exception as exc:
            rows = [{"feld": "Hinweis", "wert": f"Dateinamen nicht abrufbar ({type(exc).__name__})"},
                    {"feld": "Plan", "wert": str(args.get("plan", ""))}]
    return {"titel": title, "ziel": target, "zeilen": rows}


def _expire() -> list[dict]:
    """Abgelaufene Vorlagen schliessen (Aufrufer haelt _lock); gibt sie zum Speichern zurueck."""
    now, expired = time.time(), []
    for a in _items.values():
        if a["status"] == "WAITING_FOR_APPROVAL" and now - a["erstellt"] > TTL:
            a["status"] = "CANCELLED"
            a["ergebnis"] = "Vorlage abgelaufen."
            expired.append(a)
    return expired


def _create(tool: str, args: dict, handler) -> dict:
    key = _normalize(args)
    a = _create_locked(tool, args, handler, key, _preview(tool, args))  # Vorschau ausserhalb der Sperre (Netz)
    _save(a)
    return a


def _create_locked(tool: str, args: dict, handler, key: str, preview: dict | None = None) -> dict:
    with _lock:
        _expire()
        for a in _items.values():  # gleiche Vorlage schon offen? Dann wiederverwenden.
            if a["werkzeug"] == tool and a["schluessel"] == key and a["status"] == "WAITING_FOR_APPROVAL":
                return a
        ctx = _current() or {}
        a = {"id": uuid.uuid4().hex[:8], "werkzeug": tool, "argumente": dict(args), "schluessel": key,
             "vorschau": preview or _preview(tool, args), "status": "WAITING_FOR_APPROVAL", "erstellt": time.time(),
             "ergebnis": "", "_handler": handler, "quelle": ctx.get("quelle"), "zug": ctx.get("zug")}
        done_at = previously_executed(tool, key)
        if done_at is not None:
            when = time.strftime("%d.%m. %H:%M", time.localtime(done_at))
            a["duplikat"] = when
            a["vorschau"]["zeilen"].insert(0, {"feld": "Achtung", "wert": f"Identische Aktion wurde am {when} bereits ausgeführt."})
        _items[a["id"]] = a
        if len(_items) > 100:  # alte, erledigte Eintraege aufraeumen
            for old in sorted(_items.values(), key=lambda x: x["erstellt"])[:20]:
                if old["status"] != "WAITING_FOR_APPROVAL":
                    _items.pop(old["id"], None)
        return a


def _run(a: dict, via: str) -> str:
    with _lock:
        if a["status"] != "WAITING_FOR_APPROVAL":
            raise ToolFailure(f"Diese Vorlage ist bereits {status_text(a['status'])} – nichts wurde doppelt ausgeführt.")
        a["status"] = "RUNNING"
        handler = a.get("_handler") or _handlers.get(a["werkzeug"])
    _save(a)  # RUNNING sichern, bevor nach aussen gesendet wird (Absturz -> UNKLAR, nie doppelt)
    if handler is None:  # Vorlage stammt von vor einem Neustart
        _ensure_handlers()
        handler = _handlers.get(a["werkzeug"])
    try:
        if handler is None:
            raise ToolFailure("Werkzeug für diese Vorlage ist nicht verfügbar (nicht konfiguriert?).")
        out = handler(**a["argumente"], bestaetigt_vom_nutzer=True)
    except Exception as exc:
        a["status"], a["ergebnis"] = "FAILED", str(exc)
        _save(a)
        audit.record(via, a["werkzeug"], {**a["argumente"], "bestaetigt_vom_nutzer": True}, "fehler", str(exc))
        if isinstance(exc, ToolFailure):
            raise
        raise ToolFailure(f"{type(exc).__name__}: {exc}") from exc
    a["status"], a["ergebnis"] = "COMPLETED", str(out)
    _save(a)
    _record_executed(a)
    from .tools import clear_cache

    clear_cache()  # Daten haben sich geaendert: keine veralteten Leseergebnisse mehr ausliefern
    if via == "klick":  # Ueber Stimme/Chat protokolliert der Agent selbst
        audit.record(via, a["werkzeug"], {**a["argumente"], "bestaetigt_vom_nutzer": True}, "ok", str(out))
    return str(out)


def status_text(status: str) -> str:
    return {"WAITING_FOR_APPROVAL": "wartet auf Freigabe", "RUNNING": "läuft", "COMPLETED": "erledigt",
            "FAILED": "fehlgeschlagen", "CANCELLED": "verworfen",
            UNCLEAR: "unklar – bitte prüfen"}.get(status, status)


def wrap(tool: Tool) -> Tool:
    """Macht aus einem schreibenden Werkzeug eines mit Vorlage + Freigabe."""
    original = tool.handler
    _handlers[tool.name] = original

    def handler(**kw):
        ok = bool(kw.pop("bestaetigt_vom_nutzer", False))
        if not ok:
            a = _create(tool.name, kw, original)
            rows = "\n".join(f"{r['feld']}: {r['wert']}" for r in a["vorschau"]["zeilen"])
            if click_only():
                return (f"VORLAGE erstellt, noch NICHT ausgeführt (Freigabe-ID {a['id']}). Der Nutzer sieht sie als Karte:\n"
                        f"{a['vorschau']['titel']} ({a['vorschau']['ziel']})\n{rows}\n"
                        "Fasse sie ihm kurz zusammen. Freigeben kann er nur selbst per Klick auf 'Senden' "
                        "(Einstellung: Freigabe nur per Klick). Bei Änderungswünschen erstellst du eine neue Vorlage.")
            return (f"VORLAGE erstellt, noch NICHT ausgeführt (Freigabe-ID {a['id']}). Der Nutzer sieht sie als Karte:\n"
                    f"{a['vorschau']['titel']} ({a['vorschau']['ziel']})\n{rows}\n"
                    f"Fasse sie ihm kurz zusammen und frag, ob du sie so ausführen sollst. Nach seinem klaren Ja rufst du "
                    f"{tool.name} mit exakt denselben Angaben und bestaetigt_vom_nutzer=true auf. "
                    "Er kann auch selbst auf 'Senden' klicken. Bei Änderungswünschen erstellst du eine neue Vorlage.")
        ctx = _current()
        if click_only():
            raise ToolFailure("Freigabe nur per Klick: Der Nutzer muss die Vorlage selbst über 'Senden' freigeben.")
        if ctx is not None and ctx.get("kanal") == "hintergrund":
            raise ToolFailure("Hintergrund-Aufträge dürfen nichts freigeben. Die Vorlage wartet auf den Nutzer.")
        key = _normalize(kw)
        with _lock:
            expired = _expire()
            match = next((a for a in _items.values() if a["werkzeug"] == tool.name and a["schluessel"] == key
                          and a["status"] == "WAITING_FOR_APPROVAL"), None)
            done = next((a for a in _items.values() if a["werkzeug"] == tool.name and a["schluessel"] == key
                         and a["status"] == "COMPLETED"), None)
        _save(*expired)
        if match is None:
            if done is not None:
                raise ToolFailure("Diese Aktion wurde bereits ausgeführt (z. B. per Klick). Nicht doppelt ausführen.")
            raise ToolFailure("Freigabe fehlt: Erstelle zuerst eine Vorlage (gleicher Aufruf mit "
                              "bestaetigt_vom_nutzer=false), zeig sie dem Nutzer und warte auf sein Ja.")
        if (ctx is not None and match.get("zug") is not None and match.get("quelle") == ctx.get("quelle")
                and ctx.get("zug") is not None and ctx["zug"] <= match["zug"]):
            audit.record(ctx.get("kanal") or "stimme/chat", tool.name, {**kw, "bestaetigt_vom_nutzer": True},
                         "abgelehnt", "Freigabe im selben Zug wie die Vorlage")
            raise ToolFailure("Freigabe fehlt: Die Vorlage ist gerade erst entstanden. Zeig sie dem Nutzer und "
                              "warte auf sein Ja in einer NEUEN Nachricht – im selben Zug ist keine Freigabe möglich.")
        try:
            out = _run(match, "stimme/chat")
        finally:
            if match["status"] != "WAITING_FOR_APPROVAL":
                _notify_resolved(match["id"])
        from . import jobs

        jobs.resolve_approval(match["id"], True)
        return out

    return Tool(tool.name, tool.description + " Erster Aufruf mit bestaetigt_vom_nutzer=false erzeugt eine Vorlage "
                "für den Nutzer; ausgeführt wird erst nach seinem Ja mit identischen Angaben und true.",
                tool.properties, handler, tool.required)


def _notify_resolved(item_id: str) -> None:
    a = _items.get(item_id)
    for fn in list(_resolved):
        try:
            fn(item_id, a["status"] if a else "UNBEKANNT")
        except Exception:
            pass


def approve(item_id: str) -> str:
    a = _items.get(item_id)
    if a is None:
        raise ToolFailure("Vorlage nicht gefunden (abgelaufen oder Jarvis neu gestartet).")
    try:
        out = _run(a, "klick")
    finally:
        if a["status"] != "WAITING_FOR_APPROVAL":
            _notify_resolved(item_id)
    from . import jobs

    jobs.resolve_approval(item_id, True)
    for fn in list(_listeners):
        try:
            fn(f"Der Nutzer hat die Vorlage '{a['vorschau']['titel']}' per Klick freigegeben. Ergebnis: {out}")
        except Exception:
            pass
    return out


def reject(item_id: str) -> None:
    with _lock:
        a = _items.get(item_id)
        if not a or a["status"] != "WAITING_FOR_APPROVAL":
            return
        a["status"], a["ergebnis"] = "CANCELLED", "Vom Nutzer verworfen."
    _save(a)
    from . import jobs

    jobs.resolve_approval(item_id, False)
    audit.record("klick", a["werkzeug"], a["argumente"], "abgebrochen", "verworfen")
    _notify_resolved(item_id)
    for fn in list(_listeners):
        try:
            fn(f"Der Nutzer hat die Vorlage '{a['vorschau']['titel']}' verworfen.")
        except Exception:
            pass


def listing(limit: int = 20) -> list[dict]:
    with _lock:
        expired = _expire()
        items = sorted(_items.values(), key=lambda x: x["erstellt"], reverse=True)[:limit]
        out = [{"id": a["id"], "werkzeug": a["werkzeug"], "vorschau": a["vorschau"], "status": a["status"],
                "status_text": status_text(a["status"]), "ergebnis": a["ergebnis"][:300],
                "erstellt": a["erstellt"], "duplikat": a.get("duplikat")} for a in items]
    _save(*expired)
    return out


def restore(saved: list[dict]) -> int:
    """Gespeicherte Vorlagen laden. RUNNING (Ausfuehrung lief beim Neustart) -> UNKLAR, nie automatisch wiederholen.
    Die Zug-Bindung entfaellt: Eine Vorlage von vor dem Neustart stammt immer aus einem frueheren Zug."""
    unclear = []
    with _lock:
        for a in saved:
            if not isinstance(a, dict) or "id" not in a or a["id"] in _items:
                continue
            a["zug"] = None
            a["_handler"] = None  # wird bei Bedarf ueber _handlers (Werkzeugname) aufgeloest
            a.setdefault("ergebnis", "")
            if a.get("status") == "RUNNING":
                a["status"], a["ergebnis"] = UNCLEAR, UNCLEAR_TEXT
                unclear.append(a)
            _items[a["id"]] = a
        expired = _expire()
    _save(*unclear, *expired)
    return len(saved)


def _ensure_handlers() -> None:
    """Registriert die Original-Handler aller schreibenden Werkzeuge (all_tools ruft wrap() auf)."""
    try:
        from .tools import all_tools

        all_tools({})
    except Exception as exc:  # fehlende Integrationen duerfen den Start nicht stoppen
        print(f"[freigaben] Werkzeuge nicht geladen: {type(exc).__name__}")


def vorlage(tool: str, args: dict) -> dict:
    """Legt für ein freigabepflichtiges Werkzeug eine Vorlage an, ohne es auszuführen (z. B. aus dem CRM).

    Ausgeführt wird wie immer erst nach „Freigeben“; ein unbekanntes oder nicht eingerichtetes Werkzeug ergibt
    einen ToolFailure statt einer leeren Vorlage.
    """
    _ensure_handlers()
    handler = _handlers.get(tool)
    if handler is None:
        raise ToolFailure(f"Für „{tool}“ ist keine Freigabe-Aktion eingerichtet.")
    return _create(tool, dict(args), handler)


def on_decision(fn) -> None:
    _listeners.append(fn)


def on_resolved(fn) -> None:
    if fn not in _resolved:
        _resolved.append(fn)
