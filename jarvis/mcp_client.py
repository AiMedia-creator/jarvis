"""MCP-Client: externe MCP-Server als Jarvis-Werkzeuge (Idee und Protokoll aus OpenJarvis ``mcp``, Apache-2.0).

Konfiguration in ``~/.jarvis/mcp.json`` (fehlt die Datei, passiert nichts)::

    {
      "server": {
        "dateien": {
          "befehl": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/Users/omar/Documents"],
          "nur_lesen": ["read_file", "list_directory", "search_files"],
          "aus": false
        },
        "notion": {"url": "https://mcp.example.com/mcp", "header": {"Authorization": "Bearer …"}}
      }
    }

- Werkzeugnamen: ``mcp_<server>_<werkzeug>``.
- ``nur_lesen``: ``true`` (alle Werkzeuge lesen nur) oder eine Liste von Werkzeugnamen. Alles andere gilt als
  Aktion nach aussen und laeuft ueber Vorlage + Freigabe (approvals.py) – wie Mail senden.
- ``erlaubt`` (optional): nur diese Werkzeuge des Servers ueberhaupt anbieten.
- Ergebnisse laufen durch den Injection-Scanner (schutz.py).
Unterstuetzt: stdio (``befehl``) und Streamable HTTP (``url``). MCP-Protokollversion 2025-03-26.
"""

from __future__ import annotations

import itertools
import json
import os
import queue
import re
import subprocess
import threading
import time
from pathlib import Path

from . import config
from .tools import Tool, ToolFailure

PROTOCOL = "2025-03-26"
START_TIMEOUT = 25.0
CALL_TIMEOUT = 60.0
MAX_RESULT = 15000

_lock = threading.Lock()
_clients: dict[str, "Client"] = {}
_failed: dict[str, str] = {}


def config_path() -> Path:
    return config.data_dir() / "mcp.json"


def load_config() -> dict:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    servers = data.get("server") or data.get("servers") or data.get("mcpServers") or {}
    return {k: v for k, v in servers.items() if isinstance(v, dict) and not v.get("aus")}


class McpError(Exception):
    pass


class _Stdio:
    def __init__(self, cmd: list[str], env: dict | None):
        full_env = {**os.environ, **{str(k): str(v) for k, v in (env or {}).items()}}
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1, env=full_env)
        self.lines: queue.Queue = queue.Queue()
        threading.Thread(target=self._read, daemon=True, name="jarvis-mcp-read").start()

    def _read(self):
        for line in self.proc.stdout:  # type: ignore[union-attr]
            self.lines.put(line)
        self.lines.put(None)

    def request(self, msg: dict, timeout: float) -> dict:
        self.notify(msg)
        deadline = time.monotonic() + timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise McpError("Zeitüberschreitung")
            try:
                line = self.lines.get(timeout=left)
            except queue.Empty:
                raise McpError("Zeitüberschreitung") from None
            if line is None:
                raise McpError("Server beendet")
            try:
                data = json.loads(line)
            except ValueError:
                continue  # Log-Zeilen auf stdout ignorieren
            if isinstance(data, dict) and data.get("id") == msg["id"]:
                return data

    def notify(self, msg: dict) -> None:
        if self.proc.poll() is not None:
            raise McpError("Server läuft nicht")
        self.proc.stdin.write(json.dumps(msg) + "\n")  # type: ignore[union-attr]
        self.proc.stdin.flush()  # type: ignore[union-attr]

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


class _Http:
    def __init__(self, url: str, headers: dict | None):
        from urllib.parse import urlsplit

        parts = urlsplit(url)
        local = parts.hostname in {"127.0.0.1", "localhost", "::1"}
        # Hostname echt auswerten (kein Regex): verhindert Tricks wie http://localhost:@evil.example/
        if parts.username is not None or parts.password is not None or not parts.hostname or \
                not (parts.scheme == "https" or (parts.scheme == "http" and local)):
            raise McpError("MCP über HTTP nur mit https:// oder lokal (127.0.0.1/localhost), ohne Zugangsdaten in der URL")
        self.url, self.headers, self.session_id = url, {str(k): str(v) for k, v in (headers or {}).items()}, None

    def _post(self, msg: dict, timeout: float):
        import requests

        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", **self.headers}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        r = requests.post(self.url, json=msg, headers=headers, timeout=timeout)
        if r.headers.get("Mcp-Session-Id"):
            self.session_id = r.headers["Mcp-Session-Id"]
        if r.status_code >= 400:
            raise McpError(f"HTTP {r.status_code}")
        return r

    def request(self, msg: dict, timeout: float) -> dict:
        r = self._post(msg, timeout)
        body = r.text
        if "text/event-stream" in r.headers.get("Content-Type", ""):
            for line in body.splitlines():
                if line.startswith("data:"):
                    try:
                        data = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    if data.get("id") == msg["id"]:
                        return data
            raise McpError("keine Antwort im Ereignisstrom")
        return r.json()

    def notify(self, msg: dict) -> None:
        self._post(msg, 15)

    def close(self):
        pass


class Client:
    def __init__(self, name: str, spec: dict):
        self.name, self.spec = name, spec
        self._ids = itertools.count(1)
        self._lock = threading.Lock()
        if spec.get("befehl") or spec.get("command"):
            cmd = spec.get("befehl") or [spec["command"], *spec.get("args", [])]
            self.transport = _Stdio([str(c) for c in cmd], spec.get("env"))
        elif spec.get("url"):
            self.transport = _Http(str(spec["url"]), spec.get("header") or spec.get("headers"))
        else:
            raise McpError("weder 'befehl' noch 'url' angegeben")
        self.call("initialize", {"protocolVersion": PROTOCOL, "capabilities": {},
                                 "clientInfo": {"name": "jarvis", "version": "1.0"}}, START_TIMEOUT)
        self.transport.notify({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.tools = self.call("tools/list", {}, START_TIMEOUT).get("tools", [])

    def call(self, method: str, params: dict, timeout: float = CALL_TIMEOUT) -> dict:
        with self._lock:
            data = self.transport.request({"jsonrpc": "2.0", "id": next(self._ids), "method": method,
                                           "params": params}, timeout)
        if data.get("error"):
            raise McpError(str(data["error"].get("message", data["error"]))[:300])
        return data.get("result") or {}

    def close(self):
        self.transport.close()


def _client(name: str, spec: dict) -> Client | None:
    with _lock:
        if name in _clients:
            return _clients[name]
        if name in _failed:
            return None
    try:
        client = Client(name, spec)
    except Exception as exc:
        with _lock:
            _failed[name] = f"{type(exc).__name__}: {exc}"[:200]
        print(f"[mcp] Server {name} nicht verfügbar: {_failed[name]}")
        return None
    with _lock:
        _clients[name] = client
    return client


def _safe(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]", "_", text).strip("_").lower()


def _result_text(result: dict) -> str:
    parts = []
    for item in result.get("content") or []:
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        elif item.get("type") == "resource":
            res = item.get("resource") or {}
            parts.append(str(res.get("text") or f"[Ressource {res.get('uri', '')}]"))
        else:
            parts.append(f"[{item.get('type', 'Inhalt')} nicht als Text darstellbar]")
    text = "\n".join(parts).strip() or "(leeres Ergebnis)"
    if len(text) > MAX_RESULT:
        text = text[:MAX_RESULT] + "\n[... gekürzt]"
    if result.get("isError"):
        raise ToolFailure(f"MCP-Werkzeug meldet Fehler: {text[:1500]}")
    return text


def _make_tool(client: Client, spec: dict, mcp_tool: dict) -> Tool | None:
    from . import approvals, policy

    raw = str(mcp_tool.get("name", ""))
    if not raw:
        return None
    allowed = spec.get("erlaubt")
    if isinstance(allowed, list) and raw not in allowed:
        return None
    name = f"mcp_{_safe(client.name)}_{_safe(raw)}"[:64]
    schema = mcp_tool.get("inputSchema") or {}
    props = dict(schema.get("properties") or {})
    required = [r for r in schema.get("required") or [] if r in props]
    ro = spec.get("nur_lesen")
    read_only = ro is True or (isinstance(ro, list) and raw in ro)

    def handler(**kw):
        kw.pop("bestaetigt_vom_nutzer", None)
        try:
            return _result_text(client.call("tools/call", {"name": raw, "arguments": kw}))
        except McpError as exc:
            raise ToolFailure(f"MCP-Server {client.name}: {exc}") from None

    desc = f"[MCP {client.name}] {str(mcp_tool.get('description', '')).strip()}"[:1000]
    if read_only:
        policy.MCP_READ.add(name)
    else:
        props["bestaetigt_vom_nutzer"] = {"type": "boolean", "description": "false = Vorlage zur Freigabe; true nur nach Ja"}
        required = [*required, "bestaetigt_vom_nutzer"]
        approvals.ACTIONS.setdefault(name, (f"MCP-Aktion {raw}", f"Server {client.name}"))
        approvals.PREVIEWS.setdefault(name, lambda args: [{"feld": k, "wert": str(v)[:600]} for k, v in args.items()])
        desc += " (wirkt nach aussen: erst Vorlage mit bestaetigt_vom_nutzer=false)"
    return Tool(name, desc, props, handler, required)


def tools() -> list[Tool]:
    servers = load_config()
    if not servers:
        return []
    out: list[Tool] = []
    for name, spec in servers.items():
        client = _client(name, spec)
        if client is None:
            continue
        for t in client.tools:
            tool = _make_tool(client, spec, t)
            if tool is not None:
                out.append(tool)
    return out


def status() -> list[str]:
    lines = []
    for name in load_config():
        if name in _clients:
            lines.append(f"{name}: verbunden, {len(_clients[name].tools)} Werkzeuge")
        elif name in _failed:
            lines.append(f"{name}: Fehler – {_failed[name]}")
        else:
            lines.append(f"{name}: noch nicht gestartet")
    return lines


def close_all() -> None:
    with _lock:
        for c in _clients.values():
            c.close()
        _clients.clear()
        _failed.clear()
