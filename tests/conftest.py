"""Gemeinsame Test-Einstellungen: eigenes Home-Verzeichnis, keine echten Schluessel, keine Netzwerkzugriffe."""

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_HOME = tempfile.mkdtemp(prefix="jarvis-test-home-")
os.environ["HOME"] = _HOME
for var in ("ANTHROPIC_API_KEY", "ELEVENLABS_API_KEY", "HUBSPOT_TOKEN", "ANTHROPIC_WORKSPACE_ID", "ELEVENLABS_VOICE_ID"):
    os.environ.pop(var, None)
os.environ["ANTHROPIC_API_KEY"] = "test-key"
os.environ["JARVIS_NO_BROWSER"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _frischer_cache():
    """Kurzzeit-Cache der Werkzeuge zwischen Tests leeren."""
    from jarvis.tools import clear_cache

    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def mock_server():
    """Startet einen kleinen HTTP-Server; Antworten liefert eine Funktion (method, path, body) -> (code, obj|bytes)."""
    servers = []

    def start(responder):
        log = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _any(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                body = json.loads(raw) if raw and raw[:1] in b"{[" else None
                log.append({"method": self.command, "path": self.path, "body": body, "headers": dict(self.headers)})
                code, obj = responder(self.command, self.path, body)
                data = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST = do_PUT = do_PATCH = _any

        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}", log

    yield start
    for s in servers:
        s.shutdown()
