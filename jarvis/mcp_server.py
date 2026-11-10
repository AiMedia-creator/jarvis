"""Jarvis-MCP-Server (stdio) für Claude Code: Jarvis-Daten abfragen und Erinnerungen speichern/suchen.

Start (von Claude Code als lokaler Prozess): ``python -m jarvis.mcp_server``. Kein Netzwerk-Port.

Richtung und Grenzen:
- Claude Code (dieser Mac) → Jarvis: ja, über stdio. Claude.ai im Browser kann das NICHT nutzen – dort sind nur
  öffentlich erreichbare Remote-Server (HTTPS + OAuth) möglich; einen offenen Endpunkt bietet Jarvis bewusst nicht an.
- Lesend: Anrufe, Kennzahlen, Content, Jobs, Gedächtnis.
- Schreibend NUR im Gedächtnis: `jarvis_gedaechtnis_speichern` legt eine Erinnerung mit Herkunft (Quelle "mcp",
  Client-Name, Zeit) an; `jarvis_gedaechtnis_loeschen` löscht ausschließlich Einträge, die selbst über MCP entstanden
  sind. Keine Aufträge, keine Mails, kein CRM. Mit ``JARVIS_MCP_NUR_LESEN=1`` gibt es gar keine Schreibwerkzeuge.
- Jeder Aufruf wird in ~/.jarvis/mcp-zugriffe.jsonl protokolliert (Werkzeug, Zeit, Client, schreibend – ohne Inhalte).
"""

from __future__ import annotations

import json
import os
import sys
import time

from . import config

PROTOCOL = "2024-11-05"
_client = "unbekannt"  # Name des MCP-Clients aus initialize (z. B. "claude-code")
READ_TOOLS = {
    "jarvis_anruf_kennzahlen": ("Gemessene Anruf-Kennzahlen der letzten Tage (nur lesend).",
                                {"tage": {"type": "integer", "description": "Zeitraum, Standard 7"}}, []),
    "jarvis_anrufe": ("Letzte erfasste Anrufe mit Ergebnis, Notiz und nächstem Schritt (nur lesend).",
                      {"tage": {"type": "integer", "description": "Zeitraum, Standard 7"}}, []),
    "jarvis_content": ("Content-Einträge mit Status, Hooks und Quellen (nur lesend).", {}, []),
    "jarvis_jobs": ("Letzte Agenten-Jobs aus der Jarvis-Datenbank mit Status, Schritten und Fehlern (nur lesend).", {}, []),
    "jarvis_gedaechtnis_suchen": ("Sucht im Jarvis-Gedächtnis: dauerhaftes Wissen und Gesprächsarchiv, je mit Quelle und "
                                  "Zeitstempel (nur lesend). Leere Treffer heißen: nichts gefunden – nichts erfinden.",
                                  {"frage": {"type": "string", "description": "Suchbegriff"}}, []),
}
WRITE_TOOLS = {
    "jarvis_gedaechtnis_speichern": (
        "Speichert EINE dauerhafte Erinnerung im Jarvis-Gedächtnis (Ziel, Präferenz, Entscheidung, Projekt, Person). "
        "Nur ausdrücklich vom Nutzer genannte oder bestätigte Inhalte; keine Zugangsdaten. Herkunft (MCP, Client, Zeit) "
        "wird mitgespeichert. Antwort nennt Duplikat oder Widerspruch zu älteren Einträgen.",
        {"inhalt": {"type": "string", "description": "die Erinnerung in einem klaren Satz"},
         "kategorie": {"type": "string", "description": "note, preference, project, firma, lektion oder eigene Kategorie"}},
        ["inhalt"]),
    "jarvis_gedaechtnis_loeschen": (
        "Löscht eine Erinnerung, die über MCP gespeichert wurde (ID aus speichern/suchen). Einträge aus anderen "
        "Quellen kann nur der Nutzer im Jarvis-Dashboard löschen.",
        {"id": {"type": "string", "description": "ID der Erinnerung"}}, ["id"]),
}


def read_only() -> bool:
    return os.getenv("JARVIS_MCP_NUR_LESEN", "").lower() in {"1", "true", "ja"}


def tools() -> dict:
    return dict(READ_TOOLS) if read_only() else {**READ_TOOLS, **WRITE_TOOLS}


TOOLS = tools()  # Rückwärtskompatibel für Importe; maßgeblich ist tools() zum Aufrufzeitpunkt.


def _log(name: str) -> None:
    try:
        path = config.data_dir() / "mcp-zugriffe.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"zeit": time.strftime("%Y-%m-%dT%H:%M:%S"), "werkzeug": name, "client": _client,
                                 "schreibend": name in WRITE_TOOLS}, ensure_ascii=False) + "\n")
        path.chmod(0o600)
    except OSError:
        pass


def call(name: str, args: dict) -> dict:
    from . import anrufe, content, memory

    def tage() -> int:
        try:
            return max(1, min(int(args.get("tage") or 7), 365))
        except (TypeError, ValueError):
            return 7

    if name == "jarvis_anruf_kennzahlen":
        return anrufe.kennzahlen(tage())
    if name == "jarvis_anrufe":
        return {"anrufe": anrufe.anrufe_liste(None, tage(), 50)}
    if name == "jarvis_content":
        return content.uebersicht()
    if name == "jarvis_jobs":
        from . import persist

        return {"jobs": [{k: j.get(k) for k in ("id", "titel", "art", "status", "start", "ende", "fehler")}
                         | {"schritte": [{"werkzeug": s.get("werkzeug"), "status": s.get("status")} for s in j.get("schritte", [])]}
                         for j in reversed(persist.load_jobs(30))],
                "hinweis": "Stand aus SQLite; laufende Jobs des Jarvis-Servers erscheinen mit ihrem zuletzt gespeicherten Status."}
    if name == "jarvis_gedaechtnis_suchen":
        data = memory.store().center(str(args.get("frage", ""))[:500], 10)
        return {"wissen": [{k: f[k] for k in ("id", "content", "category", "source", "client", "created_at", "updated_at",
                                               "confirmed_at", "conflicts")} for f in data["wissen"]],
                "archiv": [{k: e[k] for k in ("id", "conversation_id", "kanal", "user_text", "assistant_text", "status",
                                               "created_at")} for e in data["archiv"]],
                "gefunden": bool(data["wissen"] or data["archiv"])}
    if name == "jarvis_gedaechtnis_speichern":
        try:
            saved = memory.store().remember(str(args.get("inhalt", "")), str(args.get("kategorie") or "note"), "mcp",
                                            client=_client)
        except ValueError as exc:
            raise ValueError("Nicht gespeichert: leer, zu lang oder enthält Zugangsdaten.") from exc
        return {"id": saved["id"], "gespeichert": not saved.get("duplicate"), "duplikat": bool(saved.get("duplicate")),
                "widerspruch_zu": saved.get("conflicts") or [], "quelle": "mcp", "client": _client,
                "zeit": saved.get("updated_at") or time.strftime("%Y-%m-%dT%H:%M:%S")}
    if name == "jarvis_gedaechtnis_loeschen":
        ident = str(args.get("id", ""))
        try:
            current = memory.store().history(ident)["aktuell"]
        except KeyError:
            return {"geloescht": False, "hinweis": "Keine Erinnerung mit dieser ID."}
        if current.get("source") != "mcp":
            return {"geloescht": False, "hinweis": "Dieser Eintrag stammt nicht aus MCP – nur im Jarvis-Dashboard löschbar."}
        report = memory.store().forget_report(ident)
        return {"geloescht": bool(report.get("deleted")), "hinweis": "Erinnerung, Versionen und Suchindex-Eintrag entfernt."}
    raise KeyError(name)


def handle(msg: dict) -> dict | None:
    method, ident = msg.get("method"), msg.get("id")
    if ident is None:  # Benachrichtigung
        return None
    def ok(result): return {"jsonrpc": "2.0", "id": ident, "result": result}
    def err(code, text): return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": text}}
    if method == "initialize":
        global _client
        info = (msg.get("params") or {}).get("clientInfo") or {}
        _client = "".join(ch for ch in str(info.get("name") or "unbekannt") if ch.isalnum() or ch in "-_. ")[:40] or "unbekannt"
        return ok({"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                   "serverInfo": {"name": "jarvis", "version": "2.0"}})
    if method == "tools/list":
        return ok({"tools": [{"name": n, "description": d,
                              "inputSchema": {"type": "object", "properties": p, "required": r},
                              "annotations": {"readOnlyHint": n in READ_TOOLS}}
                             for n, (d, p, r) in tools().items()]})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        if name not in tools():
            return err(-32602, "Unbekanntes Werkzeug")
        _log(name)
        try:
            data = call(name, params.get("arguments") or {})
        except Exception as exc:  # Fehler sichtbar, nie still
            return ok({"isError": True, "content": [{"type": "text", "text": f"Fehler: {type(exc).__name__}: {exc}"}]})
        return ok({"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]})
    if method == "ping":
        return ok({})
    return err(-32601, "Methode nicht unterstützt")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            reply = handle(json.loads(line))
        except json.JSONDecodeError:
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Ungültiges JSON"}}
        if reply is not None:
            sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
