"""Lesender MCP-Server (stdio) für Claude Code: macht Jarvis-Daten abfragbar, ohne etwas zu ändern.

Start (von Claude Code als lokaler Prozess): ``python -m jarvis.mcp_server``. Kein Netzwerk-Port, keine Schreibwerkzeuge.
Werkzeuge: jarvis_anruf_kennzahlen, jarvis_anrufe, jarvis_content, jarvis_gedaechtnis_suchen. Jeder Aufruf wird in
~/.jarvis/mcp-zugriffe.jsonl protokolliert (Werkzeug, Zeit, ohne Inhalte). Claude.ai (Web) kann das NICHT nutzen: Dort
sind nur öffentlich erreichbare Remote-Server möglich.
"""

from __future__ import annotations

import json
import sys
import time

from . import config

PROTOCOL = "2024-11-05"
TOOLS = {
    "jarvis_anruf_kennzahlen": ("Gemessene Anruf-Kennzahlen der letzten Tage (nur lesend).",
                                {"tage": {"type": "integer", "description": "Zeitraum, Standard 7"}}),
    "jarvis_anrufe": ("Letzte erfasste Anrufe mit Ergebnis, Notiz und nächstem Schritt (nur lesend).",
                      {"tage": {"type": "integer", "description": "Zeitraum, Standard 7"}}),
    "jarvis_content": ("Content-Einträge mit Status, Hooks und Quellen (nur lesend).", {}),
    "jarvis_gedaechtnis_suchen": ("Sucht im Jarvis-Gedächtnis (Fakten, Gesprächsarchiv) mit Quelle (nur lesend).",
                                  {"frage": {"type": "string", "description": "Suchbegriff"}}),
}


def _log(name: str) -> None:
    try:
        path = config.data_dir() / "mcp-zugriffe.jsonl"
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"zeit": time.strftime("%Y-%m-%dT%H:%M:%S"), "werkzeug": name}) + "\n")
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
    if name == "jarvis_gedaechtnis_suchen":
        return memory.snapshot(str(args.get("frage", ""))[:500], 10)
    raise KeyError(name)


def handle(msg: dict) -> dict | None:
    method, ident = msg.get("method"), msg.get("id")
    if ident is None:  # Benachrichtigung
        return None
    def ok(result): return {"jsonrpc": "2.0", "id": ident, "result": result}
    def err(code, text): return {"jsonrpc": "2.0", "id": ident, "error": {"code": code, "message": text}}
    if method == "initialize":
        return ok({"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                   "serverInfo": {"name": "jarvis-lesend", "version": "1.0"}})
    if method == "tools/list":
        return ok({"tools": [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": p}}
                             for n, (d, p) in TOOLS.items()]})
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        if name not in TOOLS:
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
