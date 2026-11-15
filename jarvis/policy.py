"""Technisch erzwungene Werkzeug-Policy: eine Prüfstelle für alle Kanäle (Text, Stimme, Echtzeit, Hintergrund).

Jeder Werkzeugaufruf wird klassifiziert:
- read      nur lesen, keine Nebenwirkung
- external  Aktion nach außen (Mail, Termin, CRM) – läuft zusätzlich über Vorlage + Freigabe (approvals.py)
- write     eigene Daten ändern ohne Freigabe (Aufgabe, Entwurf, Dokument, Gedächtnis, Hintergrundjob)
- local     auf dem Mac etwas öffnen (Programm, Webseite)
Unbekannte Werkzeuge gelten als ``write`` (sichere Voreinstellung).

Für write/local führt das Ausführungsprotokoll (SQLite ``tool_ledger``) jeden Aufruf mit Status
STARTED -> DONE | FAILED | UNCLEAR. Daraus folgt:
- Doppelte Zustellung (gleiches Werkzeug, gleiche Angaben innerhalb kurzer Zeit) führt nichts doppelt aus.
- Läuft ein identischer Aufruf noch oder ist sein Ausgang unbekannt (Absturz, Netzfehler mitten in der Aktion),
  wird er NICHT wiederholt; Status "unklar" geht an Modell und Nutzer (Abgleich statt Doppelversuch).
- Pro Nutzerzug ist die Zahl schreibender Aufrufe begrenzt; Hintergrundaufträge dürfen nichts lokal öffnen.
Die Prüfung findet im Code statt – ein Modellprompt kann sie nicht aufheben.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from typing import Any, Callable

from . import persist
from .tools import Tool, ToolFailure

READ = {
    "mails_suchen", "mail_lesen", "kalender_termine", "drive_suchen", "drive_lesen", "aufgaben_liste",
    "kontakte_suchen", "hubspot_kontakt_suchen", "hubspot_deals_suchen", "hubspot_deals_zu_kontakt",
    "hubspot_notizen_lesen", "nachrichten", "telegram_neuigkeiten", "telegram_suchen", "lies_skill",
    "unternehmer_strategie", "web_suche", "wissen_suchen", "webseite_lesen", "drive_uebersicht", "zwischenablage_lesen", "n8n_workflows", "auftraege_liste", "kalender_struktur", "ablaeufe_status", "n8n_status",
    # aus OpenJarvis uebernommen, alle nur lesend
    "rechnen", "wetter", "hacker_news", "github_benachrichtigungen", "pdf_lesen", "notizen_suchen", "notiz_lesen",
    "obsidian_suchen", "obsidian_lesen", "tiefenrecherche", "mehr_faehigkeiten",
    "live_kamera", "kamera_schliessen",  # zeigen nur eine Ansicht in der Oberflaeche
    "github_suchen",  # oeffentliche Suche, nur lesend
    "projekt_liste", "anruf_karte", "anruf_kennzahlen", "content_liste",  # Anruf-Copilot, nur lesend
    "sheet_lesen",  # Google Sheets, nur lesend
    "gbrain_suchen", "browser_seite", "browser_bild", "browser_tabs", "browser_scrollen",  # Chrome lesen/scrollen (browser.py)
    "systemone_pruefen",  # strukturierter, lesender Ja/Nein-Check über TypeSafe Jev
}
# MCP-Werkzeuge, die in ~/.jarvis/mcp.json als "nur_lesen" markiert sind, meldet mcp_client hier an.
MCP_READ: set[str] = set()
LOCAL = {"seite_oeffnen", "programm_oeffnen", "hubspot_im_crm_oeffnen", "in_zwischenablage",
         "browser_oeffnen"}  # öffnet eine Adresse im echten Chrome (nie aus Hintergrundaufträgen)
MEMORY_READ_COMMANDS = {"view"}

DEDUP_WINDOW = {"write": 10 * 60, "local": 20}   # Sekunden, in denen ein identischer Aufruf als Duplikat gilt
UNCLEAR_BLOCK = 24 * 3600                          # so lange blockiert ein unklarer Ausgang den gleichen Aufruf
MAX_WRITES_PER_TURN = 12

_lock = threading.Lock()
_turn_writes: dict[tuple[str, int], int] = {}


def kind(name: str, args: dict | None = None, tool: Tool | None = None) -> str:
    if tool is not None and "bestaetigt_vom_nutzer" in tool.properties:
        return "external"
    if name == "memory":
        return "read" if str((args or {}).get("command", "")) in MEMORY_READ_COMMANDS else "write"
    if name in READ or name in MCP_READ:
        return "read"
    if name in LOCAL:
        return "local"
    return "write"


def fingerprint(name: str, args: dict | None) -> str:
    clean = {k: (v.strip() if isinstance(v, str) else v) for k, v in (args or {}).items()
             if v not in (None, "")}
    payload = name + "\n" + json.dumps(clean, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:40]


def _short_result(value: Any) -> str:
    from .memory import _redact

    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return _redact(text)[:400]


def _last(fp: str) -> dict | None:
    with persist._connect() as c:
        row = c.execute("SELECT id, status, started, ended, result FROM tool_ledger WHERE fingerprint=? "
                        "ORDER BY started DESC LIMIT 1", (fp,)).fetchone()
    return None if row is None else {"id": row[0], "status": row[1], "started": row[2], "ended": row[3], "result": row[4]}


def _write(entry_id: str, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    with persist._connect() as c:
        c.execute(f"UPDATE tool_ledger SET {cols} WHERE id=?", (*fields.values(), entry_id))


def _clock(ts: float) -> str:
    return time.strftime("%H:%M", time.localtime(ts))


def execute(tool: Tool, args: dict, run: Callable[[], Any] | None = None, *, source: str = "",
            conversation: str = "", turn: int | None = None, channel: str = "") -> Any:
    """Führt einen Werkzeugaufruf unter der Policy aus. ``run`` überschreibt den Standard ``tool.run(args)``."""
    args = args if isinstance(args, dict) else {}
    category = kind(tool.name, args, tool)
    inner = run or (lambda: tool.run(args))

    def runner():
        from . import events

        started = time.perf_counter()
        try:
            value = inner()
        except Exception as exc:
            events.log("werkzeug", werkzeug=tool.name, klasse=category, quelle=source, status="fehler",
                       fehlerart=type(exc).__name__, ms=round((time.perf_counter() - started) * 1000))
            raise
        events.log("werkzeug", werkzeug=tool.name, klasse=category, quelle=source, status="ok",
                   ms=round((time.perf_counter() - started) * 1000))
        return value
    if category == "local" and channel == "hintergrund":
        raise ToolFailure("Hintergrund-Aufträge dürfen auf dem Mac nichts öffnen (keine Nutzerinteraktion).")
    if category in ("read", "external"):
        return runner()  # external: Vorlage + Freigabe erzwingt approvals.py

    if turn is not None and category == "write":
        key = (conversation, int(turn))
        with _lock:
            _turn_writes[key] = _turn_writes.get(key, 0) + 1
            if len(_turn_writes) > 500:
                _turn_writes.pop(next(iter(_turn_writes)))
            if _turn_writes[key] > MAX_WRITES_PER_TURN:
                raise ToolFailure(f"Grenze erreicht: höchstens {MAX_WRITES_PER_TURN} schreibende Aktionen pro Auftrag. "
                                  "Frag den Nutzer, ob du weitermachen sollst.")

    fp = fingerprint(tool.name, args)
    entry = uuid.uuid4().hex
    now = time.time()
    with _lock:  # Prüfen + Eintragen atomar, damit parallele Zustellungen nicht beide durchkommen
        last = _last(fp)
        if last is not None:
            age = now - float(last["started"])
            if last["status"] == "STARTED":
                raise ToolFailure(f"Diese Aktion läuft bereits seit {_clock(last['started'])} Uhr oder ihr Ausgang ist "
                                  "unklar – nicht erneut ausgeführt. Ergebnis abwarten oder im Zielsystem prüfen.")
            if last["status"] == "UNCLEAR" and age < UNCLEAR_BLOCK:
                raise ToolFailure(f"Status unklar: Eine identische Aktion von {_clock(last['started'])} Uhr wurde "
                                  "unterbrochen. Nicht automatisch wiederholen – bitte zuerst im Zielsystem prüfen "
                                  "und den Nutzer fragen.")
            if last["status"] == "DONE" and age < DEDUP_WINDOW[category]:
                return (f"Bereits ausgeführt um {_clock(last['started'])} Uhr (identische Anfrage erkannt, nicht doppelt "
                        f"ausgeführt). Ergebnis damals: {last['result']}")
        with persist._connect() as c:
            c.execute("INSERT INTO tool_ledger (id, fingerprint, tool, kind, status, source, conversation, turn, started) "
                      "VALUES (?,?,?,?,?,?,?,?,?)",
                      (entry, fp, tool.name, category, "STARTED", source[:40], conversation[:200], turn, now))
            c.execute("DELETE FROM tool_ledger WHERE id IN (SELECT id FROM tool_ledger ORDER BY started DESC "
                      "LIMIT -1 OFFSET 2000)")
    try:
        result = runner()
    except ToolFailure as exc:
        _write(entry, status="FAILED", ended=time.time(), result=_short_result(str(exc)))
        raise
    except Exception as exc:
        # Netz- oder Dienstfehler mitten in der Aktion: Ausgang unbekannt -> nie blind wiederholen.
        _write(entry, status="UNCLEAR", ended=time.time(), result=f"{type(exc).__name__}")
        raise ToolFailure(f"Ausgang unklar ({type(exc).__name__}). Nicht wiederholen, bevor im Zielsystem geprüft "
                          "wurde, ob die Aktion angekommen ist.") from exc
    _write(entry, status="DONE", ended=time.time(), result=_short_result(result))
    return result


def restore() -> int:
    """Beim Start: was noch als STARTED markiert ist, wurde durch Absturz/Neustart unterbrochen -> UNCLEAR."""
    try:
        with persist._connect() as c:
            cur = c.execute("UPDATE tool_ledger SET status='UNCLEAR', ended=?, result='Neustart während der "
                            "Ausführung' WHERE status='STARTED'", (time.time(),))
            return cur.rowcount
    except Exception as exc:  # darf den Start nicht verhindern
        print(f"[policy] Protokoll nicht wiederhergestellt: {type(exc).__name__}")
        return 0


def recent(limit: int = 30) -> list[dict]:
    with persist._connect() as c:
        rows = c.execute("SELECT tool, kind, status, source, conversation, started, ended, result FROM tool_ledger "
                         "ORDER BY started DESC LIMIT ?", (max(1, min(200, limit)),)).fetchall()
    keys = ("werkzeug", "art", "status", "quelle", "gespraech", "start", "ende", "ergebnis")
    return [dict(zip(keys, row)) for row in rows]
