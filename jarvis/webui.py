"""Jarvis-Oberflaeche im Browser: python -m jarvis --ui  ->  http://localhost:8765

Laeuft nur auf deinem Mac (127.0.0.1), nichts ist von aussen erreichbar.
"""

from __future__ import annotations

import datetime
import html
import json
import os
import threading
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config

PORT = int(config.get("ui_port", "8765") or 8765)
UI_FILE = Path(__file__).resolve().parent / "ui" / "index.html"
_UI = Path(__file__).resolve().parent / "ui"
# Feste Liste statischer Dateien (kein freier Dateizugriff ueber die URL)
VENDOR = {"/vendor/elevenlabs-client.js": _UI / "vendor" / "elevenlabs-client.js",
          "/voicecore.js": _UI / "voicecore.js", "/app.js": _UI / "app.js",
          "/memory-ui.js": _UI / "memory-ui.js", "/theme.css": _UI / "theme.css"}
ALLOWED_HOSTS = {f"localhost:{PORT}", f"127.0.0.1:{PORT}"}
# Ein Host: Die Oberfläche lebt im Dashboard (Command Center auf 8080). Diese Seite leitet nur noch weiter;
# die API-Endpunkte hier bleiben für ältere Clients erreichbar.
DASHBOARD_URL = f"http://localhost:{int(os.getenv('PORT', '8080') or 8080)}/"
REDIRECT_HTML = ("<!doctype html><html lang=\"de\"><head><meta charset=\"utf-8\"><title>Jarvis</title>"
                 f"<meta http-equiv=\"refresh\" content=\"0; url={DASHBOARD_URL}\"></head><body>"
                 f"<p>Jarvis ist umgezogen: <a href=\"{DASHBOARD_URL}\">Command Center öffnen</a></p></body></html>").encode()

_agent = None
_agent_lock = threading.Lock()


def _note_for_agent(text: str) -> None:
    if _agent is not None and hasattr(_agent, "notes"):
        _agent.notes.append(text)


from . import approvals  # noqa: E402

approvals.on_decision(_note_for_agent)


def make_agent(channel: str, conversation_id: str):
    """Agent fuer Hintergrund- und Nebengespraeche (in Tests ersetzbar)."""
    if str(config.get("demo", "false")).lower() in {"true", "1", "yes"}:
        from .v3_bridge import DemoAgent
        return DemoAgent(channel=channel, conversation_id=conversation_id)
    from .agent import Agent

    return Agent(channel=channel, model=config.get("model") or "sonnet", conversation_id=conversation_id)


def _factory(channel: str, conversation_id: str):
    from . import runtime

    return agent() if conversation_id == runtime.MAIN else make_agent(channel, conversation_id)


def agent():
    global _agent
    with _agent_lock:
        if _agent is None:
            # Standard: Sonnet (gute Antworten bei gutem Tempo; umstellbar in den Einstellungen).
            _agent = make_agent(channel="web", conversation_id="haupt")
        return _agent


from . import runtime as _runtime  # noqa: E402

_runtime.set_factory(_factory)


# --- Heute-Uebersicht (direkt aus den Diensten, ohne Claude = kostenlos und schnell) ---

def _termine() -> list[dict]:
    from .tools.google import _service

    cal = _service("calendar", "v3")
    start = datetime.datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + datetime.timedelta(days=1)
    items = cal.events().list(calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
                              singleEvents=True, orderBy="startTime", maxResults=20).execute().get("items", [])
    out = []
    for ev in items:
        s = ev["start"].get("dateTime")
        e = ev["end"].get("dateTime")
        zeit = "ganztägig"
        if s:
            zeit = datetime.datetime.fromisoformat(s).strftime("%H:%M")
            if e:
                zeit += "–" + datetime.datetime.fromisoformat(e).strftime("%H:%M")
        out.append({"zeit": zeit, "titel": ev.get("summary", "(ohne Titel)"), "ort": ev.get("location", "")})
    return out


def _mails() -> list[dict]:
    from .tools.google import _headers, _service

    gmail = _service("gmail", "v1")
    refs = gmail.users().messages().list(userId="me", q="is:unread in:inbox newer_than:2d",
                                         maxResults=8).execute().get("messages", [])
    out = []
    for ref in refs:
        msg = gmail.users().messages().get(userId="me", id=ref["id"], format="metadata",
                                           metadataHeaders=["From", "Subject"]).execute()
        h = _headers(msg)
        sender = h.get("from", "")
        if "<" in sender:
            sender = sender.split("<")[0].strip().strip('"') or sender
        out.append({"id": msg["id"], "von": sender, "betreff": h.get("subject", "(kein Betreff)"),
                    "auszug": html.unescape(msg.get("snippet", ""))[:140]})
    return out


def _news() -> list[dict]:
    from .tools.news import fetch_items

    items, _ = fetch_items("allgemein", "", 6)
    return [{"quelle": i["quelle"], "titel": i["titel"], "link": i["link"],
             "zeit": i["zeit"].astimezone().strftime("%H:%M") if i["zeit"] else ""} for i in items]


def _deals() -> list[dict]:
    from .tools import hubspot

    data = hubspot._call("POST", "/crm/v3/objects/deals/search", json={
        "limit": 6, "properties": hubspot.DEAL_PROPS,
        "sorts": [{"propertyName": "closedate", "direction": "ASCENDING"}],
        "filterGroups": [{"filters": [{"propertyName": "hs_is_closed", "operator": "EQ", "value": "false"}]}]})
    stages = hubspot._stage_labels()
    out = []
    for d in data.get("results", []):
        p = d.get("properties", {})
        try:
            betrag = f"{float(p['amount']):,.0f} €".replace(",", ".") if p.get("amount") else ""
        except ValueError:
            betrag = p.get("amount") or ""
        out.append({"name": p.get("dealname", "(ohne Namen)"), "betrag": betrag,
                    "phase": stages.get(p.get("dealstage") or "", p.get("dealstage") or "").split(" (")[0],
                    "abschluss": (p.get("closedate") or "")[:10]})
    return out


def overview() -> dict:
    from .agent import german_date
    from .tools import google, hubspot

    if str(config.get("demo", "false")).lower() in {"true", "1", "yes"}:
        return {"datum": german_date(datetime.date.today()), "google": False, "hubspot": False,
                "news": [], "termine": [], "mails": [], "deals": [], "demo": True}
    result: dict = {"datum": german_date(datetime.date.today()), "google": google.is_connected(),
                    "hubspot": hubspot.is_configured()}
    jobs = {"news": _news}
    if result["google"]:
        jobs.update(termine=_termine, mails=_mails)
    if result["hubspot"]:
        jobs["deals"] = _deals

    def run(key, fn):
        try:
            result[key] = fn()
        except Exception as exc:
            result[key + "_fehler"] = str(exc)[:200]

    threads = [threading.Thread(target=run, args=(k, f)) for k, f in jobs.items()]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=25)
    return result


def status() -> dict:
    from . import echtzeit

    return echtzeit.status(agent().model if _agent is not None else None)


def _eleven_available() -> bool:
    from . import echtzeit

    return echtzeit.eleven_available()


def _eleven_problem():
    from . import echtzeit

    return echtzeit.eleven_problem()


def save_settings(data: dict) -> dict:
    from . import echtzeit

    def set_model(model_id: str) -> None:
        agent().model = model_id

    return echtzeit.save_settings(data, set_model)


# --- HTTP ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    server_version = "Jarvis"

    def log_message(self, fmt, *args):  # Terminal ruhig halten
        pass

    def _host_ok(self) -> bool:
        return self.headers.get("Host", "") in ALLOWED_HOSTS

    def _json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Browser hat die Anfrage schon verlassen (z. B. Seite neu geladen)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 200_000:
            raise ValueError("Anfrage zu gross")
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        if not self._host_ok():
            return self._json(403, {"fehler": "nur lokal"})
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            body = REDIRECT_HTML
            self.send_response(302)
            self.send_header("Location", DASHBOARD_URL)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path in VENDOR and VENDOR[path].exists():
            body = VENDOR[path].read_bytes()
            self.send_response(200)
            kind = "text/css" if path.endswith(".css") else "text/javascript"
            self.send_header("Content-Type", f"{kind}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400" if "vendor" in path else "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/status":
            self._json(200, status())
        elif path == "/api/jobs":
            from . import jobs

            self._json(200, {"jobs": jobs.recent()})
        elif path == "/api/telemetry":
            from . import telemetry

            self._json(200, telemetry.summary())
        elif path == "/api/approvals":
            self._json(200, {"vorlagen": approvals.listing()})
        elif path == "/api/events":
            from urllib.parse import parse_qs, urlparse

            from . import runtime

            q = parse_qs(urlparse(self.path).query)
            after = int((q.get("after") or ["0"])[0] or 0)
            timeout = min(25.0, float((q.get("timeout") or ["0"])[0] or 0))
            events, seq = runtime.bus.since(after, timeout)
            self._json(200, {"events": events[-300:], "seq": seq})
        elif path == "/api/audit":
            from . import audit

            self._json(200, {"eintraege": audit.tail(100)})
        elif path == "/api/aktionen":
            from . import policy

            self._json(200, {"aktionen": policy.recent(100)})
        elif path == "/api/ereignisse":
            from . import events

            self._json(200, {"ereignisse": events.tail(200)})
        elif path == "/api/overview":
            self._json(200, overview())
        elif path == "/api/memory":
            from urllib.parse import parse_qs, urlparse
            from . import memory
            values = parse_qs(urlparse(self.path).query)
            try:
                limit = max(1, min(50, int((values.get("limit") or ["12"])[0])))
            except ValueError:
                return self._json(400, {"fehler": "ungültiges Limit"})
            query = (values.get("q") or [""])[0][:1000]
            return self._json(200, memory.snapshot(query, limit))
        else:
            self._json(404, {"fehler": "unbekannt"})

    def do_POST(self):
        # Schutz: andere Webseiten duerfen deinen Jarvis nicht fernsteuern.
        if not self._host_ok() or self.headers.get("X-Jarvis") != "1":
            return self._json(403, {"fehler": "nur lokal"})
        path = self.path.split("?")[0]
        try:
            data = self._body()
        except ValueError as exc:
            return self._json(400, {"fehler": str(exc)})
        if path == "/api/memory":
            from . import memory
            try:
                content = data.get("content")
                if not isinstance(content, str) or len(content) > 4000:
                    raise ValueError("Ungültige Notiz")
                result = memory.store().remember(content, category=str(data.get("category", "note")), source="user")
                return self._json(200, {"ok": True, "fact": result})
            except ValueError:
                return self._json(400, {"fehler": "Notiz ungültig oder enthält vertrauliche Zugangsdaten"})
        if path == "/api/ask":
            return self._ask(str(data.get("text", "")).strip(), str(data.get("conversation_id") or "haupt")[:40])
        if path == "/api/jobs":
            from . import runtime

            text = str(data.get("text", "")).strip()
            if not text:
                return self._json(400, {"fehler": "leer"})
            return self._json(200, {"job": runtime.start_background(text)})
        if path == "/api/jobs/cancel":
            from . import runtime

            return self._json(200, {"abgebrochen": runtime.cancel_job(str(data.get("id", "")))})
        if path == "/api/jobs/extern":
            return self._json(200, self._extern_job(data))
        if path == "/api/relevanz":
            from . import bargein

            return self._json(200, bargein.should_interrupt(str(data.get("text", ""))[:500],
                                                            str(data.get("kontext", ""))[:600]))
        if path == "/api/telemetry/client":
            from . import telemetry

            return self._json(200, {"ok": telemetry.client_report(str(data.get("job_id", "")), data)})
        if path == "/api/tts":
            return self._tts(str(data.get("text", "")).strip(), data.get("stimme"))
        if path == "/api/reset":
            agent().cancel()
            agent().reset()
            return self._json(200, {"ok": True})
        if path == "/api/stop":
            from . import jobs, runtime

            if data.get("alle"):
                ids = [j["id"] for j in jobs.recent(50) if j["status"] in (jobs.RUNNING, jobs.WAITING)]
                for jid in ids:
                    runtime.cancel_job(jid)
                return self._json(200, {"ok": True, "abgebrochen": ids})
            conv = str(data.get("conversation_id") or runtime.MAIN)
            if conv == runtime.MAIN and _agent is None:
                return self._json(200, {"ok": True, "abgebrochen": jobs.cancel_running(conv)})
            return self._json(200, {"ok": True, "abgebrochen": runtime.cancel_conversation(conv)})
        if path == "/api/eleven/session":
            if str(config.get("demo", "false")).lower() in {"true", "1", "yes"}:
                return self._json(403, {"fehler": "Externe Sprachsitzungen sind im Demo-Modus deaktiviert"})
            from . import eleven_agent

            try:
                pause = float(data.get("pause", 1.0))
                return self._json(200, eleven_agent.prepare_session(pause))
            except Exception as exc:
                traceback.print_exc()
                return self._json(502, {"fehler": str(exc)[:500]})
        if path == "/api/tool":
            if str(config.get("demo", "false")).lower() in {"true", "1", "yes"}:
                return self._json(200, {"ergebnis": "Demo: Keine externen Werkzeuge oder Konten aufgerufen."})
            from . import eleven_agent

            name = str(data.get("name", ""))
            args = data.get("args") or {}
            if not isinstance(args, dict):
                args = {}
            from . import approvals

            with approvals.context("echtzeit", approvals.current_turn("echtzeit"), "echtzeit"):
                result = self._tool_in_job(name, args, str(data.get("job_id") or ""))
            return self._json(200, {"ergebnis": result})
        if path in ("/api/approvals/approve", "/api/approvals/reject"):
            from .tools import ToolFailure

            item = str(data.get("id", ""))
            try:
                if path.endswith("approve"):
                    return self._json(200, {"ergebnis": approvals.approve(item), "vorlagen": approvals.listing()})
                approvals.reject(item)
                return self._json(200, {"vorlagen": approvals.listing()})
            except ToolFailure as exc:
                return self._json(409, {"fehler": str(exc), "vorlagen": approvals.listing()})
        if path == "/api/settings":
            try:
                return self._json(200, save_settings(data))
            except (ValueError, TypeError) as exc:
                return self._json(400, {"fehler": str(exc)})
        self._json(404, {"fehler": "unbekannt"})

    def do_DELETE(self):
        if not self._host_ok() or self.headers.get("X-Jarvis") != "1":
            return self._json(403, {"fehler": "nur lokal"})
        path = self.path.split("?")[0]
        if not path.startswith("/api/memory/"):
            return self._json(404, {"fehler": "unbekannt"})
        from . import memory
        import re
        ident = path[len("/api/memory/"):]
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", ident):
            return self._json(400, {"fehler": "ungültige Notiz-ID"})
        return self._json(200, {"ok": bool(memory.store().forget(ident))})

    def _ask(self, text: str, conversation_id: str = "haupt") -> None:
        if not text:
            return self._json(400, {"fehler": "leer"})
        from . import runtime

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        write_lock = threading.Lock()
        state = {"job": None, "gone": False}

        def emit(obj: dict) -> None:
            state["job"] = state["job"] or obj.get("job_id")
            if state["gone"]:
                return
            with write_lock:
                try:
                    self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    state["gone"] = True
                    if state["job"]:
                        runtime.cancel_job(state["job"])  # nur DIESEN Job abbrechen, nie einen anderen

        runtime.run(conversation_id, text, sink=emit)
        self.close_connection = True

    def _tool_in_job(self, name: str, args: dict, job_id: str) -> str:
        """Werkzeug fuer den ElevenLabs-Echtzeitmodus (gemeinsame Umsetzung in echtzeit.py)."""
        from . import echtzeit

        return echtzeit.tool_in_job(name, args, job_id)

    def _extern_job(self, data: dict) -> dict:
        from . import echtzeit

        return echtzeit.extern_job(data)

    def _tts(self, text: str, voice=None) -> None:
        if not text or len(text) > 2000:
            return self._json(400, {"fehler": "Text fehlt oder zu lang"})
        if str(config.get("demo", "false")).lower() in {"true", "1", "yes"}:
            import io
            import wave
            output = io.BytesIO()
            with wave.open(output, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(16000)
                wav.writeframes(b"\x00\x00" * 1600)
            audio = output.getvalue()
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
            return
        from . import tts

        if voice is not None and voice not in {x["id"] for x in tts.voices()}:
            voice = None
        try:
            audio = tts.synthesize(text, voice)
        except Exception as exc:
            return self._json(500, {"fehler": str(exc)[:300]})
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Content-Length", str(len(audio)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(audio)


def serve(open_browser: bool = True) -> None:
    from . import persist

    restored = persist.restore()  # Jobs/Freigaben vom letzten Lauf (laufende Jobs -> FAILED, nichts wird wiederholt)
    if restored["jobs"] or restored["freigaben"]:
        print(f"Wiederhergestellt: {restored['jobs']} Jobs, {restored['freigaben']} Freigaben.")
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    server.daemon_threads = True
    url = f"http://localhost:{PORT}"
    print(f"JARVIS-Oberfläche läuft: {url}   (Strg+C beendet)")
    threading.Thread(target=agent, daemon=True).start()  # Agent schon mal vorbereiten
    if open_browser and not os.getenv("JARVIS_NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nOberfläche beendet.")
    finally:
        server.server_close()
