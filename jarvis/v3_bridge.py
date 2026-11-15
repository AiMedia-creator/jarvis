"""Lokale V3-Brücke: vorhandener Agent + Gedächtnis hinter dem Java-Dashboard.

Start: python -m jarvis.v3_bridge. JARVIS_BRIDGE_TOKEN muss gesetzt sein.
Die Brücke bindet ausschließlich Loopback; keine API-Schlüssel gehen an Browser.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import hmac
import json
import logging
import os
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from aiohttp import web

from . import config

TTS_SLOT_WAIT = 15.0  # Sekunden, die ein Satz auf einen freien Vertonungsplatz wartet
_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _demo() -> bool:
    return str(config.get("demo", "false")).lower() in {"true", "1", "yes"}


class DemoAgent:
    """Explizite Offline-Demo; echtes lokales Gedächtnis, keine simulierten Tools."""
    model = "demo/local-memory"

    def __init__(self, channel="web", conversation_id="haupt"):
        self.channel = channel
        self.conversation_id = conversation_id
        self.notes = []
        self.messages = []
        self.lock = threading.Lock()
        self.cancel_event = threading.Event()
        self._job = None

    def cancel(self):
        self.cancel_event.set()

    def reset(self):
        self.cancel()
        with self.lock:
            self.messages.clear()

    def reload(self):
        pass

    def ask(self, text, on_sentence=None, on_tool=None, on_token=None, on_step=None, job=None, cancel_signal=None,
            attachments=None):
        from . import jobs, memory
        from .agent import Cancelled
        self.cancel()
        with self.lock:
            self.cancel_event.clear()
            self._job = job or jobs.create(text, self.conversation_id)
            explicit = re.match(r"\s*(?:merke dir|merk dir|speichere|notiere)\s*[:,-]?\s*(.+)", text, re.I | re.S)
            if explicit:
                memory.store().remember(explicit.group(1), category="note", source="user")
                answer = "[DEMO] Die Notiz wurde dauerhaft im lokalen Gedächtnis gespeichert. Keine externe Aktion ausgeführt."
            else:
                found = memory.store().search(text, limit=3).get("facts", [])
                if not found:
                    found = memory.store().search("", limit=2).get("facts", [])
                facts = " ".join(item.get("content", "") for item in found)
                answer = "[DEMO] " + ("Aus meinem lokalen Gedächtnis: " + facts if facts else "Lokales Gedächtnis ist bereit.")
                answer += " Für echte KI-Antworten den Echtmodus aktivieren."
            parts = re.split(r"(?<=[.!?])\s+", answer)
            spoken = []
            try:
                for part in parts:
                    if self.cancel_event.wait(.12) or (cancel_signal is not None and cancel_signal.is_set()):
                        raise Cancelled()
                    if on_token:
                        on_token(part + " ")
                    if on_sentence:
                        on_sentence(part)
                    spoken.append(part)
                memory.store().record_turn(self.conversation_id, text, answer, status="completed")
                self.messages.extend([{"role": "user", "content": text}, {"role": "assistant", "content": answer}])
                self.messages = self.messages[-24:]
                jobs.finish(self._job, jobs.COMPLETED)
                return answer
            except Cancelled:
                memory.store().record_turn(self.conversation_id, text, " ".join(spoken), status="interrupted")
                jobs.finish(self._job, jobs.CANCELLED)
                raise
            except Exception:
                jobs.finish(self._job, jobs.FAILED)
                raise


@dataclass
class RequestState:
    request_id: str
    conversation_id: str
    cancelled: threading.Event = field(default_factory=threading.Event)
    agent: object | None = None
    finished: bool = False
    cancel_sent: bool = False


class Bridge:
    def __init__(self, token: str, demo: bool | None = None):
        if len(token) < 32:
            raise ValueError("JARVIS_BRIDGE_TOKEN benötigt mindestens 32 zufällige Zeichen")
        self.token = token
        self.demo = _demo() if demo is None else demo
        self.tts_active_until = 0.0  # monotonic: bis wann Jarvis voraussichtlich spricht
        self.stt_active_until = 0.0  # monotonic: bis wann ein Gespräch als aktiv gilt
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix="jarvis-brain")
        self.cancel_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="jarvis-cancel")
        self.speech_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-whisper")
        self.tts_pool = concurrent.futures.ThreadPoolExecutor(max_workers=3, thread_name_prefix="jarvis-tts")
        self.requests: dict[str, RequestState] = {}
        self.agents: dict[str, object] = {}
        self.active_requests: dict[int, str] = {}
        self.guard = threading.Lock()
        self.slots = asyncio.Semaphore(16)
        self.speech_slots = asyncio.Semaphore(1)
        self.tts_slots = asyncio.Semaphore(3)
        self.whisper_models: dict = {}  # je Whisper-Modellname einmal geladen

    @staticmethod
    async def json_body(request):
        raw = await request.read()
        if len(raw) > 16_384:
            raise web.HTTPRequestEntityTooLarge(max_size=16_384, actual_size=len(raw))
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise web.HTTPBadRequest(text="JSON-Objekt erforderlich")
        return data

    def cancel(self, state: RequestState):
        with self.guard:
            if state.finished or state.cancel_sent:
                return
            state.cancel_sent = True
            state.cancelled.set()
            agent = state.agent
            stream = (getattr(agent, "_active_stream", None)
                      if agent is not None and self.active_requests.get(id(agent)) == state.request_id else None)
        if stream is not None:
            # Das erfasste SDK-Objekt schließen, niemals später agent.cancel() auf dem nächsten Zug.
            def close_captured():
                with contextlib.suppress(Exception):
                    stream.close()
            self.cancel_pool.submit(close_captured)

    def current_agent(self, conversation_id: str):
        cache_key = "v3-" + conversation_id
        with self.guard:
            cached = self.agents.get(cache_key)
        if cached is not None:
            return cached
        # Aufwendige Client-/SQLite-Initialisierung niemals unter dem Cancellation-Lock.
        if self.demo:
            candidate = DemoAgent(conversation_id=conversation_id)
        else:
            from .agent import Agent
            candidate = Agent(channel="web", model=config.get("model") or "sonnet", conversation_id=conversation_id)
        with self.guard:
            if len(self.agents) >= 64:
                active = {"v3-" + state.conversation_id for state in self.requests.values()}
                for old in list(self.agents):
                    if old not in active:
                        del self.agents[old]
                        break
            return self.agents.setdefault(cache_key, candidate)

    async def generate(self, request: web.Request):
        data = await self.json_body(request)
        text = data.get("text")
        conversation_id = data.get("conversation_id")
        request_id = data.get("request_id")
        if not isinstance(text, str) or not text.strip() or len(text) > 8000:
            raise web.HTTPBadRequest(text="Ungültiger Text")
        if not isinstance(conversation_id, str) or not _ID.fullmatch(conversation_id):
            raise web.HTTPBadRequest(text="Ungültiges Gespräch")
        if not isinstance(request_id, str) or not _ID.fullmatch(request_id):
            raise web.HTTPBadRequest(text="Ungültiger Request")
        if self.slots.locked():
            raise web.HTTPTooManyRequests(text="Agenten-Auslastungsgrenze")
        await self.slots.acquire()
        state = RequestState(request_id, conversation_id)
        with self.guard:
            if request_id in self.requests:
                self.slots.release()
                raise web.HTTPConflict(text="Request bereits vorhanden")
            previous = [s for s in self.requests.values() if s.conversation_id == conversation_id]
            self.requests[request_id] = state
        for old in previous:
            self.cancel(old)
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=256)

        def emit(event: dict):
            if state.cancelled.is_set():
                return
            def enqueue():
                if state.cancelled.is_set():
                    return
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    self.cancel(state)
            loop.call_soon_threadsafe(enqueue)

        def work():
            from . import jobs, runtime
            job = None
            try:
                if state.cancelled.is_set():
                    return
                agent = self.current_agent(conversation_id)
                with self.guard:
                    state.agent = agent
                    self.active_requests[id(agent)] = state.request_id
                if state.cancelled.is_set():
                    return
                from . import anhaenge

                attached = anhaenge.take(conversation_id)
                job = jobs.create(text, agent.conversation_id)
                runtime._job_agents[job["id"]] = agent
                runtime.bus.publish({"typ": "job", "job": runtime._public(job), "job_id": job["id"], "conversation_id": agent.conversation_id})
                streamed = []

                def token(value):
                    streamed.append(True)
                    emit({"type": "token", "text": value})
                answer = agent.ask(text, on_token=token,
                          on_tool=lambda name: (emit({"type": "log", "message": "Werkzeug: " + name}),
                                                 emit({"type": "tool", "name": name})),
                          on_step=lambda step: emit({"type": "log", "message": "Schritt: " + str(step.get("werkzeug", "")) + " / " + str(step.get("status", ""))}),
                          job=job, cancel_signal=state.cancelled,
                          **({"attachments": attached} if attached else {}))
                if answer and not streamed:
                    # Meldungen ohne Textstrom (Budget, Zeitlimit, Schrittgrenze, Ausfall) sonst stumm: hörbar machen
                    emit({"type": "token", "text": answer})
                jobs.set_result(job, answer or "")
                used = list(getattr(agent, "_used_memories", None) or [])
                # Memory v1: sichtbar machen, welche gespeicherten Erinnerungen für diese Antwort mitgeschickt wurden.
                emit({"type": "memory", "message": "Gespräch im Chatverlauf gespeichert",
                      **({"erinnerungen": used} if used else {})})
                emit({"type": "done"})
            except Exception as exc:
                from .agent import Cancelled
                if isinstance(exc, Cancelled) or state.cancelled.is_set():
                    emit({"type": "cancelled"})
                else:
                    # Provider-Fehlermeldungen können Credential-/Request-Inhalte enthalten.
                    emit({"type": "error", "message": "Agent nicht verfügbar. Lokale Konfiguration prüfen."})
            finally:
                if job is not None:
                    runtime._job_agents.pop(job["id"], None)
                    kind = "abgebrochen" if state.cancelled.is_set() else "fertig" if job.get("status") == jobs.COMPLETED else "fehler"
                    runtime.bus.publish({"typ": kind, "job": runtime._public(job), "job_id": job["id"], "conversation_id": agent.conversation_id})
                state.finished = True
                loop.call_soon_threadsafe(self.slots.release)
                with self.guard:
                    self.requests.pop(request_id, None)
                    if state.agent is not None and self.active_requests.get(id(state.agent)) == request_id:
                        self.active_requests.pop(id(state.agent), None)

        future = loop.run_in_executor(self.pool, work)
        response = web.StreamResponse(headers={"Content-Type": "application/x-ndjson; charset=utf-8", "Cache-Control": "no-store"})
        try:
            await response.prepare(request)
            while True:
                if state.cancelled.is_set():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=2)
                except asyncio.TimeoutError:
                    if future.done() and queue.empty():
                        break
                    event = {"type": "heartbeat"}
                await response.write((json.dumps(event, ensure_ascii=False) + "\n").encode())
                if event["type"] in {"done", "error", "cancelled"}:
                    break
            await response.write_eof()
        except (ConnectionError, asyncio.CancelledError, RuntimeError):
            self.cancel(state)
        finally:
            if not state.finished:
                self.cancel(state)
        return response

    # --- Dateianhänge im Chat (nur Arbeitsspeicher, siehe anhaenge.py) ---

    def _conversation(self, request) -> str:
        conversation_id = request.query.get("conversation_id", "")
        if not _ID.fullmatch(conversation_id):
            raise web.HTTPBadRequest(text="Ungültiges Gespräch")
        return conversation_id

    async def anhang_hochladen(self, request):
        from . import anhaenge, modelle

        conversation_id = self._conversation(request)
        name = request.query.get("name", "")[:200]
        # Gestreamt mit eigener Obergrenze lesen (client_max_size der App gilt nur für read()/json()).
        chunks, size = [], 0
        async for chunk in request.content.iter_chunked(65536):
            size += len(chunk)
            if size > anhaenge.MAX_FILE:
                return web.json_response({"fehler": f"Die Datei ist zu groß (höchstens {anhaenge.MAX_FILE // (1024 * 1024)} MB)."},
                                         status=413)
            chunks.append(chunk)
        try:
            result = await asyncio.to_thread(anhaenge.add, conversation_id, name, b"".join(chunks))
        except anhaenge.AttachmentError as exc:
            return web.json_response({"fehler": str(exc)}, status=415 if "nicht unterstützt" in str(exc) else 400)
        except Exception as exc:  # nie Dateiinhalt oder Pfade in die Antwort
            logging.getLogger(__name__).warning("Anhang nicht verarbeitet: %s", type(exc).__name__)
            return web.json_response({"fehler": "Die Datei konnte nicht verarbeitet werden."}, status=400)
        provider = modelle.PROVIDERS[modelle.current().provider]
        if result["typ"] in anhaenge.IMAGE_TYPES and not provider.images:
            result["hinweis"] = f"Das aktuelle Modell ({provider.name}) kann keine Bilder sehen."
        elif result["gekuerzt"]:
            result["hinweis"] = "Sehr lange Datei: nur der Anfang wird übernommen."
        return web.json_response({"anhang": result})

    async def anhaenge_liste(self, request):
        from . import anhaenge

        return web.json_response({"anhaenge": anhaenge.pending(self._conversation(request))})

    async def anhang_entfernen(self, request):
        from . import anhaenge

        removed = anhaenge.remove(self._conversation(request), request.match_info.get("id", ""))
        return web.json_response({"entfernt": removed}, status=200 if removed else 404)

    async def cancel_request(self, request):
        data = await self.json_body(request)
        with self.guard:
            state = self.requests.get(str(data.get("request_id", "")))
        if state:
            self.cancel(state)
        return web.json_response({"ok": True, "cancelled": bool(state)})

    async def news(self, request):
        """Schlagzeilen fuer die Weltansicht: Quelle, Zeit, Link und – nur bei eindeutigem Ortsnamen – ein Ort."""
        from . import geo
        from .tools import ToolFailure, news

        rubrik = request.query.get("rubrik", "allgemein")[:20]
        stichwort = request.query.get("stichwort", "")[:80]
        try:
            items, errors = await asyncio.to_thread(news.fetch_items, rubrik, stichwort, 12)
        except ToolFailure as exc:
            return web.json_response({"status": "fehler", "hinweis": str(exc), "meldungen": []})
        out = []
        for item in items:
            # Feed-Kopfzeilen ("tagesschau") sind keine Meldungen.
            if item["titel"].strip().lower() == item["quelle"].strip().lower() or len(item["titel"].strip()) < 12:
                continue
            place = geo.locate(item["titel"] + " " + item["text"])
            out.append({"quelle": item["quelle"], "titel": item["titel"], "text": item["text"],
                        "zeit": item["zeit"].isoformat() if item["zeit"] else None, "link": item["link"],
                        "ort": place})
        return web.json_response({"status": "ok" if out else "leer", "meldungen": out, "fehler": errors,
                                  "abgerufen": time.strftime("%Y-%m-%dT%H:%M:%S%z")})

    async def market(self, request):
        from . import market

        if "verzeichnis" in request.query:
            return web.json_response({"verzeichnis": market.directory()})
        return web.json_response(await asyncio.to_thread(market.quote_series, request.query.get("q", "")[:60]))

    # --- Kommandozentrale: Aufträge, Kalender, Freigaben, Jobs --------------------------------------

    async def orders_list(self, request):
        from . import orders
        return web.json_response(await asyncio.to_thread(orders.listing, True))

    async def orders_create(self, request):
        from . import orders
        data = await self.json_body(request)
        try:
            order = await asyncio.to_thread(orders.create, data.get("felder") or {},
                                            request_id=data.get("request_id"), source="dashboard")
        except orders.OrderError as exc:
            return web.json_response({"fehler": str(exc)}, status=400)
        return web.json_response({"auftrag": order})

    async def orders_update(self, request):
        from . import orders
        data = await self.json_body(request)
        try:
            version = data.get("version")
            if not isinstance(version, int):
                raise orders.OrderError("Version fehlt.")
            order = await asyncio.to_thread(orders.update, request.match_info["id"], data.get("aenderungen") or {},
                                            expected_version=version, request_id=data.get("request_id"),
                                            source="dashboard")
        except orders.Conflict as exc:
            return web.json_response({"fehler": str(exc), "aktuell": exc.current}, status=409)
        except orders.NotFound:
            return web.json_response({"fehler": "Auftrag nicht gefunden."}, status=404)
        except orders.OrderError as exc:
            return web.json_response({"fehler": str(exc)}, status=400)
        return web.json_response({"auftrag": order})

    async def calendar(self, request):
        from . import calendar_data
        if self.demo:
            return web.json_response({"status": "demo", "hinweis": "Demo-Modus: kein Kalender.", "termine": [],
                                      "konflikte": []})
        try:
            data = await asyncio.to_thread(calendar_data.events, request.query.get("von", "")[:25],
                                           request.query.get("bis", "")[:25])
        except ValueError as exc:
            return web.json_response({"status": "fehler", "hinweis": str(exc)}, status=400)
        except Exception as exc:
            from .tools import ToolFailure
            # ToolFailure-Texte sind fuer Menschen geschrieben (z. B. "Google-Verbindung abgelaufen ...") und
            # enthalten keine Zugangsdaten – die Ursache zeigen statt eines nackten "502".
            hint = str(exc)[:300] if isinstance(exc, ToolFailure) else f"Kalender nicht erreichbar ({type(exc).__name__})."
            return web.json_response({"status": "fehler", "hinweis": hint}, status=502)
        return web.json_response(data)

    @staticmethod
    def _approval_view(item: dict) -> dict:
        from . import approvals, graph, jobs
        job = next((j for j in jobs.recent(60) if any(st.get("freigabe_id") == item["id"] for st in j["schritte"])), None)
        waiting = item["id"] in graph._INDEX
        return {**item, "laeuft_ab": item["erstellt"] + approvals.TTL,
                "job": {"id": job["id"], "status": job["status"], "titel": job["titel"]} if job else None,
                "graph": "wartet" if waiting else None}

    async def approvals_list(self, request):
        from . import approvals
        items = await asyncio.to_thread(approvals.listing, 30)
        return web.json_response({"freigaben": [self._approval_view(i) for i in items]})

    async def approval_decide(self, request):
        from . import approvals
        from .tools import ToolFailure
        ident, action = request.match_info["id"], request.match_info["aktion"]
        if not _ID.fullmatch(ident) or action not in ("freigeben", "verwerfen"):
            raise web.HTTPBadRequest(text="Ungültige Freigabe")
        if self.demo:
            return web.json_response({"fehler": "Demo-Modus: keine Freigaben."}, status=403)
        try:
            if action == "freigeben":
                result = await asyncio.to_thread(approvals.approve, ident)
            else:
                await asyncio.to_thread(approvals.reject, ident)
                result = "verworfen"
        except ToolFailure as exc:
            return web.json_response({"fehler": str(exc)}, status=409)
        return web.json_response({"ergebnis": str(result)[:600]})

    async def n8n_callback(self, request):
        from .tools import n8n
        raw = await request.read()
        if len(raw) > 32_000:
            raise web.HTTPRequestEntityTooLarge(max_size=32_000, actual_size=len(raw))
        status, text = await asyncio.to_thread(n8n.handle_callback, raw, request.headers.get("X-Jarvis-Signature", ""))
        return web.json_response({"ergebnis": text}, status=status)

    async def geocode(self, request):
        from . import geocode
        if self.demo:
            return web.json_response({"status": "demo", "hinweis": "Demo-Modus: keine Ortssuche."})
        return web.json_response(await asyncio.to_thread(geocode.search, request.query.get("q", "")[:120]))

    async def jobs_list(self, request):
        from . import jobs
        return web.json_response({"jobs": await asyncio.to_thread(jobs.recent, 30)})

    # --- Agenten-Center, Verbindungen, Gedächtnis-Center, fortlaufendes Transkript --------------------------

    def _agenten(self) -> dict:
        from . import skill_laufzeit, uebersicht
        tools = self._registered_tools()
        view = uebersicht.jobs_view(50)
        view["agenten"] = uebersicht.agents()
        view["rollen"] = uebersicht.rollen(tools, skill_laufzeit.definitions(tools), view["jobs"])
        return view

    async def agenten(self, request):
        return web.json_response(await asyncio.to_thread(self._agenten))

    async def verbindungen(self, request):
        from . import verbindungen
        if self.demo:
            return web.json_response({"stand": "", "verbindungen": [], "braucht_dich": 0, "demo": True})
        force = request.query.get("neu") == "1"
        return web.json_response(await asyncio.to_thread(verbindungen.uebersicht, force))

    async def gedaechtnis(self, request):
        from . import memory
        try:
            limit = max(1, min(200, int(request.query.get("limit", "60"))))
        except ValueError:
            raise web.HTTPBadRequest(text="Ungültiges Limit")
        return web.json_response(await asyncio.to_thread(memory.store().center, request.query.get("q", "")[:400], limit))

    async def gedaechtnis_export(self, request):
        """Export als Datei unter ~/.jarvis/exports (nur für dich lesbar); kleiner Inhalt zusätzlich direkt zum Download."""
        from . import memory

        def run() -> dict:
            data = memory.store().export()
            folder = config.data_dir() / "exports"
            folder.mkdir(mode=0o700, exist_ok=True)
            path = folder / f"gedaechtnis-{time.strftime('%Y%m%d-%H%M%S')}.json"
            raw = json.dumps(data, ensure_ascii=False, indent=1)
            path.write_text(raw, encoding="utf-8")
            path.chmod(0o600)
            # Nur die letzten 5 Exporte behalten: Alte Kopien würden gelöschte Erinnerungen weitertragen.
            for old in sorted(folder.glob("gedaechtnis-*.json"))[:-5]:
                with contextlib.suppress(OSError):
                    old.unlink()
            out = {"datei": str(path), "wissen": len(data["wissen"]), "versionen": len(data["versionen"]),
                   "archiv": len(data["archiv"]), "exportiert": data["exportiert"]}
            if len(raw.encode("utf-8")) < 1_200_000:
                out["inhalt"] = data
            return out
        return web.json_response(await asyncio.to_thread(run))

    async def gedaechtnis_verlauf(self, request):
        from . import memory
        ident = request.match_info["id"]
        if not _ID.fullmatch(ident):
            return web.json_response({"fehler": "Ungültige ID."}, status=400)
        try:
            return web.json_response(await asyncio.to_thread(memory.store().history, ident))
        except KeyError:
            return web.json_response({"fehler": "Erinnerung nicht gefunden."}, status=404)

    async def gedaechtnis_aktion(self, request):
        """Schreibaktionen des Gedächtnis-Centers – nur der Nutzer im Dashboard (Session, Origin, X-Jarvis in Java)."""
        from . import memory
        action = request.match_info["aktion"]
        data = await self.json_body(request)
        store = memory.store()
        ident = str(data.get("id", ""))
        if action in ("aendern", "bestaetigen", "loeschen", "archiv_loeschen") and not _ID.fullmatch(ident):
            return web.json_response({"fehler": "Ungültige ID."}, status=400)
        try:
            if action == "speichern":
                result = await asyncio.to_thread(lambda: store.remember(str(data.get("content", "")), str(data.get("category") or "note"),
                                                                       "user", client="dashboard"))
                return web.json_response({"ok": True, "eintrag": result})
            if action == "aendern":
                result = await asyncio.to_thread(lambda: store.update_fact(ident, str(data.get("content", "")),
                                                                          data.get("category") or None, "user"))
                return web.json_response({"ok": True, "eintrag": result})
            if action == "bestaetigen":
                return web.json_response({"ok": True, "eintrag": await asyncio.to_thread(store.confirm, ident)})
            if action == "loeschen":
                return web.json_response(await asyncio.to_thread(store.forget_report, ident))
            if action == "archiv_loeschen":
                return web.json_response(await asyncio.to_thread(store.delete_episode, ident))
            if action == "alle_loeschen":
                # Endgültig: nur mit ausdrücklicher Bestätigung aus der Oberfläche (zweiter Klick).
                if data.get("bestaetigt") is not True:
                    return web.json_response({"fehler": "Bestätigung fehlt."}, status=400)
                return web.json_response({"ok": True, "geloescht": await asyncio.to_thread(store.forget_all)})
            if action == "index_neu":
                return web.json_response({"ok": True, **await asyncio.to_thread(store.rebuild_index)})
        except KeyError:
            return web.json_response({"fehler": "Eintrag nicht gefunden."}, status=404)
        except ValueError:
            return web.json_response({"fehler": "Nicht gespeichert: leer, zu lang oder enthält Zugangsdaten."}, status=400)
        return web.json_response({"fehler": "Unbekannte Aktion."}, status=400)

    async def transkript(self, request):
        """Erkannte Äußerung aus dem Echtzeit-Sprachmodus fortlaufend ins Gesprächsarchiv (nur Text, nie Audio)."""
        from . import memory
        data = await self.json_body(request)
        text, ref = str(data.get("text", "")).strip(), str(data.get("ref", ""))
        if not text or len(text) > 8000 or data.get("rolle") not in ("user", "agent") or not _ID.fullmatch(ref):
            return web.json_response({"fehler": "Ungültige Äußerung (leer, zu lang oder ohne Referenz)."}, status=400)
        if self.demo:
            return web.json_response({"status": "nicht_gespeichert", "hinweis": "Demo-Modus"})
        try:
            saved = await asyncio.to_thread(memory.store().log_utterance, "echtzeit", data["rolle"], text, "echtzeit", ref)
        except ValueError:
            return web.json_response({"fehler": "Ungültige Äußerung."}, status=400)
        return web.json_response(saved)

    async def automations(self, request):
        from . import uebersicht
        return web.json_response(await asyncio.to_thread(uebersicht.automations_status))

    # --- Aus der früheren Oberfläche (8765) übernommen: Einstellungen, Echtzeit, Jobs, Protokolle, Ereignisse ---

    def _reload_prompts(self) -> None:
        """Systemprompts der laufenden Agenten neu aufbauen (z. B. nach Wechsel der Gesprächssprache)."""
        with self.guard:
            agents = list(self.agents.values())
        webui = sys.modules.get("jarvis.webui")
        if webui is not None and getattr(webui, "_agent", None) is not None:
            agents.append(webui._agent)
        for agent in agents:
            with contextlib.suppress(Exception):
                if hasattr(agent, "reload"):
                    agent.reload()

    def _set_model(self, key: str) -> None:
        """Laufende Agenten auf das neue Modell (ggf. anderer Anbieter) umstellen, ohne das Gespräch zu verlieren."""
        with self.guard:
            agents = list(self.agents.values())
        webui = sys.modules.get("jarvis.webui")
        if webui is not None and getattr(webui, "_agent", None) is not None:
            agents.append(webui._agent)
        for agent in agents:
            with contextlib.suppress(Exception):
                if hasattr(agent, "set_model"):
                    agent.set_model(key)
                else:
                    agent.model = key

    async def settings_get(self, request):
        from . import echtzeit
        return web.json_response(await asyncio.to_thread(echtzeit.status))

    async def settings_save(self, request):
        from . import echtzeit
        data = await self.json_body(request)
        if self.demo:
            return web.json_response({"fehler": "Demo-Modus: Einstellungen sind gesperrt."}, status=403)
        try:
            return web.json_response(await asyncio.to_thread(echtzeit.save_settings, data, self._set_model, self._reload_prompts))
        except (ValueError, TypeError) as exc:
            return web.json_response({"fehler": str(exc)[:200]}, status=400)

    async def eleven_session(self, request):
        from . import echtzeit
        data = await self.json_body(request)
        if self.demo or echtzeit.demo():
            return web.json_response({"fehler": "Externe Sprachsitzungen sind im Demo-Modus deaktiviert."}, status=403)
        if not echtzeit.eleven_available():
            problem = echtzeit.eleven_problem() or "ELEVENLABS_API_KEY fehlt."
            return web.json_response({"fehler": f"Echtzeit nicht verfügbar: {problem}"}, status=409)
        from . import eleven_agent
        try:
            pause = max(0.4, min(3.0, float(data.get("pause", 1.0))))
            return web.json_response(await asyncio.to_thread(eleven_agent.prepare_session, pause))
        except Exception as exc:
            logging.getLogger(__name__).warning("ElevenLabs-Sitzung fehlgeschlagen: %s", type(exc).__name__)
            return web.json_response({"fehler": str(exc)[:300] or "ElevenLabs nicht erreichbar."}, status=502)

    async def realtime_tool(self, request):
        from . import echtzeit
        data = await self.json_body(request)
        if self.demo:
            return web.json_response({"ergebnis": "Demo: Keine externen Werkzeuge oder Konten aufgerufen."})
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
        result = await asyncio.to_thread(echtzeit.run_realtime_tool, str(data.get("name", "")), args, str(data.get("job_id") or ""))
        return web.json_response({"ergebnis": result})

    async def jobs_extern(self, request):
        from . import echtzeit
        data = await self.json_body(request)
        if data.get("aktion") not in ("start", "ende"):
            raise web.HTTPBadRequest(text="aktion muss start oder ende sein")
        return web.json_response(await asyncio.to_thread(echtzeit.extern_job, data))

    async def jobs_cancel(self, request):
        from . import echtzeit
        data = await self.json_body(request)
        ident = str(data.get("id", ""))
        if not _ID.fullmatch(ident):
            raise web.HTTPBadRequest(text="Ungültige Job-ID")
        return web.json_response({"abgebrochen": bool(await asyncio.to_thread(echtzeit.stop_jobs, [ident]))})

    async def jobs_stop_all(self, request):
        from . import echtzeit
        await self.json_body(request)
        return web.json_response({"ok": True, "abgebrochen": await asyncio.to_thread(echtzeit.stop_jobs)})

    async def relevanz(self, request):
        from . import bargein
        data = await self.json_body(request)
        result = await asyncio.to_thread(bargein.should_interrupt, str(data.get("text", ""))[:500], str(data.get("kontext", ""))[:600])
        return web.json_response(result)

    async def reset(self, request):
        await self.json_body(request)
        with self.guard:
            agents = list(self.agents.values())
        for agent in agents:
            with contextlib.suppress(Exception):
                agent.cancel()
            with contextlib.suppress(Exception):
                agent.reset()
        webui = sys.modules.get("jarvis.webui")
        if webui is not None and getattr(webui, "_agent", None) is not None:
            with contextlib.suppress(Exception):
                webui._agent.cancel()
                webui._agent.reset()
        return web.json_response({"ok": True})

    async def tts_preview(self, request):
        import base64
        from . import tts
        data = await self.json_body(request)
        text = str(data.get("text", "")).strip()
        if not text or len(text) > 300:
            raise web.HTTPBadRequest(text="Vorschautext fehlt oder ist zu lang")
        voice = data.get("stimme")
        if voice is not None and voice not in {x["id"] for x in tts.voices()}:
            raise web.HTTPBadRequest(text="unbekannte Stimme")
        if self.demo:
            return web.json_response({"fehler": "Demo-Modus: keine Stimmvorschau."}, status=403)
        try:
            audio = await asyncio.to_thread(tts.synthesize, text, voice)
        except Exception:
            return web.json_response({"fehler": "Stimme konnte nicht erzeugt werden."}, status=502)
        return web.json_response({"mime": "audio/mpeg", "audio": base64.b64encode(audio).decode("ascii")})

    async def logs(self, request):
        art = request.match_info["art"]
        if art == "audit":
            from . import audit
            return web.json_response({"eintraege": await asyncio.to_thread(audit.tail, 100)})
        if art == "aktionen":
            from . import policy
            return web.json_response({"eintraege": await asyncio.to_thread(policy.recent, 100)})
        if art == "ereignisse":
            from . import events
            return web.json_response({"eintraege": await asyncio.to_thread(events.tail, 200)})
        raise web.HTTPNotFound(text="Unbekanntes Protokoll")

    _tool_cache: tuple[float, dict] | None = None

    def _registered_tools(self) -> dict:
        """Registrierte Jarvis-Werkzeuge (kurz zwischengespeichert; MCP-Abfragen nicht bei jedem Aufruf)."""
        from . import skill_laufzeit
        from .tools import all_tools
        cached = self._tool_cache
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        tools = {t.name: t for t in all_tools(skill_laufzeit.load_active(), None)}
        self._tool_cache = (time.monotonic(), tools)
        return tools

    def _platform(self) -> dict:
        from . import cloud_skills, faehigkeiten, github_quelle, skill_laufzeit
        return {"faehigkeiten": faehigkeiten.definitions(), "laeufe": faehigkeiten.recent(25),
                "skills": skill_laufzeit.definitions(self._registered_tools()),
                "berechtigungen": faehigkeiten.BERECHTIGUNGEN, "freigabepflichtig": sorted(faehigkeiten.FREIGABEPFLICHTIG),
                "github": github_quelle.status(),
                "cloud": {"erlaubt": cloud_skills.allowed(), "verfuegbar": sorted(cloud_skills.ANTHROPIC_SKILLS),
                          "sperrgrund": cloud_skills.lock_reason(), "anbieter": "Claude API (api.anthropic.com)"}}

    async def anrufe_uebersicht(self, request):
        """Anruf-Copilot: Projekte, gemessene Kennzahlen und letzte Anrufe (nur lesend, nur lokale Daten)."""
        from . import anrufe
        try:
            tage = max(1, min(int(request.query.get("tage", "7")), 365))
        except ValueError:
            tage = 7
        return web.json_response(await asyncio.to_thread(anrufe.uebersicht, tage))

    # --- Cold-Call-CRM: Leads, Ergebnisse, Call-Session (nur lokale Daten) ---

    @staticmethod
    def _lead_fehler(exc):
        from . import leads
        if isinstance(exc, leads.Konflikt):
            return web.json_response({"fehler": str(exc), "aktuell": exc.current}, status=409)
        if isinstance(exc, leads.Duplikat):
            return web.json_response({"fehler": str(exc), "duplikat": exc.vorhanden, "hart": exc.hart}, status=409)
        if isinstance(exc, leads.NichtGefunden):
            return web.json_response({"fehler": str(exc)}, status=404)
        return web.json_response({"fehler": str(exc)}, status=400)

    async def leads_uebersicht(self, request):
        from . import leads
        try:
            q = request.query
            return web.json_response(await asyncio.to_thread(
                leads.uebersicht, q.get("status", "")[:30], q.get("q", "")[:80], int(q.get("offset", "0") or 0),
                int(q.get("limit", str(leads.SEITE_STANDARD)) or leads.SEITE_STANDARD), q.get("snapshot", "")[:64], q.get("um", "")[:64]))
        except ValueError:
            return web.json_response({"fehler": "offset und limit müssen Zahlen sein."}, status=400)
        except leads.LeadError as exc:
            return self._lead_fehler(exc)

    async def lead_detail(self, request):
        from . import leads
        try:
            return web.json_response(await asyncio.to_thread(leads.detail, request.match_info["id"]))
        except leads.LeadError as exc:
            return self._lead_fehler(exc)

    async def leads_session(self, request):
        from . import leads
        try:
            seit = float(request.query.get("seit", "0"))
        except ValueError:
            return web.json_response({"fehler": "seit muss ein Zeitstempel sein."}, status=400)
        return web.json_response(await asyncio.to_thread(leads.session_zahlen, seit))

    async def lead_anlegen(self, request):
        from . import leads
        data = await self.json_body(request)
        try:
            lead = await asyncio.to_thread(leads.anlegen, data.get("felder") or {}, data.get("request_id"),
                                           bool(data.get("duplikat_bestaetigt")))
        except leads.LeadError as exc:
            return self._lead_fehler(exc)
        return web.json_response({"lead": lead})

    async def lead_aendern(self, request):
        from . import leads
        data = await self.json_body(request)
        if not isinstance(data.get("version"), int):
            return web.json_response({"fehler": "Version fehlt."}, status=400)
        try:
            lead = await asyncio.to_thread(leads.aendern, request.match_info["id"], data.get("aenderungen") or {},
                                           data["version"], bool(data.get("duplikat_bestaetigt")))
        except leads.LeadError as exc:
            return self._lead_fehler(exc)
        return web.json_response({"lead": lead})

    async def lead_ergebnis(self, request):
        """Anruf-Ergebnis erfassen. Bei „Meeting gebucht“ entsteht auf Wunsch eine Kalender-VORLAGE (Freigabe nötig)."""
        from . import leads
        data = await self.json_body(request)
        if not isinstance(data.get("version"), int):
            return web.json_response({"fehler": "Version fehlt."}, status=400)
        try:
            result = await asyncio.to_thread(
                leads.ergebnis, request.match_info["id"], str(data.get("status") or ""), data["version"],
                data.get("request_id"), data.get("notiz") or "", data.get("followup_am") or "",
                data.get("naechster_schritt") or "", data.get("termin_beginn") or "", data.get("termin_ende") or "",
                data.get("dauer_s"))
        except leads.LeadError as exc:
            return self._lead_fehler(exc)
        if result["lead"]["status"] == "meeting_gebucht" and data.get("kalender") and not result["wiederholt"]:
            result["kalender"] = ({"status": "demo", "hinweis": "Demo-Modus: kein Kalender."} if self.demo
                                  else await asyncio.to_thread(leads.termin_vorlage, result["lead"]))
        return web.json_response(result)

    async def lead_notiz(self, request):
        from . import leads
        data = await self.json_body(request)
        try:
            return web.json_response(await asyncio.to_thread(leads.notiz_hinzufuegen, request.match_info["id"],
                                                             str(data.get("text") or ""), data.get("request_id")))
        except leads.LeadError as exc:
            return self._lead_fehler(exc)

    async def leads_import(self, request):
        from . import leads
        # Rohe CSV-Bytes (nicht JSON): so gilt das Limit von 1 MB für die Datei selbst, und kaputtes UTF-8 fällt auf.
        chunks, size = [], 0
        async for chunk in request.content.iter_chunked(65536):
            size += len(chunk)
            if size > leads.CSV_MAX_BYTES:
                return web.json_response({"fehler": f"Die CSV-Datei ist zu groß (höchstens {leads.CSV_MAX_BYTES // 1_000_000} MB)."}, status=413)
            chunks.append(chunk)
        ausfuehren = request.query.get("ausfuehren", "") in ("1", "true")
        try:
            return web.json_response(await asyncio.to_thread(leads.csv_import, b"".join(chunks), ausfuehren))
        except leads.LeadError as exc:
            return self._lead_fehler(exc)

    async def content_uebersicht(self, request):
        from . import content
        return web.json_response(await asyncio.to_thread(content.uebersicht))

    async def content_status(self, request):
        """Nur der Nutzer (Dashboard mit Session, Origin und X-Jarvis) setzt Status ab „Freigegeben“. Keine Veröffentlichung."""
        from . import content
        data = await self.json_body(request)
        if self.demo:
            return web.json_response({"fehler": "Demo-Modus: keine Änderungen."}, status=403)
        try:
            item = await asyncio.to_thread(content.status_setzen, str(data.get("id", "")), str(data.get("status", "")), "nutzer")
        except content.ContentError as exc:
            return web.json_response({"fehler": str(exc)}, status=400)
        return web.json_response({"eintrag": item})

    async def faehigkeiten_list(self, request):
        return web.json_response(await asyncio.to_thread(self._platform))

    async def faehigkeit_starten(self, request):
        from . import faehigkeiten
        from .tools import ToolFailure
        data = await self.json_body(request)
        name, args = str(data.get("name", "")), data.get("args")
        if not isinstance(args, dict):
            raise web.HTTPBadRequest(text="args muss ein Objekt sein")
        if self.demo:
            return web.json_response({"fehler": "Demo-Modus: keine Läufe."}, status=403)
        if faehigkeiten.needs_approval(name):
            return web.json_response({"fehler": "Diese Fähigkeit läuft nur über Vorlage und Freigabe im Gespräch."}, status=403)
        try:
            lauf, _ = await asyncio.to_thread(faehigkeiten.start, name, args, "dashboard")
        except ToolFailure as exc:
            return web.json_response({"fehler": str(exc)}, status=400)
        return web.json_response({"lauf": faehigkeiten._public(lauf)})

    async def skill_aktivieren(self, request):
        from . import skill_laufzeit
        data = await self.json_body(request)
        if not isinstance(data.get("aktiv"), bool):
            raise web.HTTPBadRequest(text="aktiv muss true/false sein")
        try:
            await asyncio.to_thread(skill_laufzeit.set_enabled, str(data.get("id", "")), data["aktiv"])
        except ValueError as exc:
            return web.json_response({"fehler": str(exc)}, status=400)
        self._tool_cache = None
        return web.json_response({"ok": True})

    async def faehigkeit_aktion(self, request):
        from . import faehigkeiten
        from .tools import ToolFailure
        ident, action = request.match_info["id"], request.match_info["aktion"]
        if not _ID.fullmatch(ident) or action not in ("abbrechen", "wiederholen"):
            raise web.HTTPBadRequest(text="Ungültige Aktion")
        await self.json_body(request)
        if action == "abbrechen":
            ok = await asyncio.to_thread(faehigkeiten.cancel, ident)
            return web.json_response({"abgebrochen": ok}, status=200 if ok else 409)
        try:
            return web.json_response({"lauf": await asyncio.to_thread(faehigkeiten.retry, ident)})
        except ToolFailure as exc:
            return web.json_response({"fehler": str(exc)}, status=409)

    async def events_poll(self, request):
        from . import echtzeit
        try:
            after = int(request.query.get("after", "0") or 0)
            timeout = float(request.query.get("timeout", "0") or 0)
        except ValueError:
            raise web.HTTPBadRequest(text="after/timeout ungültig")
        return web.json_response(await asyncio.to_thread(echtzeit.events_since, after, timeout))

    async def memory_get(self, request):
        from . import memory
        query = request.query.get("q", "")[:1000]
        try:
            limit = max(1, min(50, int(request.query.get("limit", "12"))))
        except ValueError:
            raise web.HTTPBadRequest(text="Ungültiges Limit")
        result = await asyncio.to_thread(memory.snapshot, query, limit)
        return web.json_response(result)

    async def memory_add(self, request):
        from . import memory
        data = await self.json_body(request)
        if not isinstance(data.get("content"), str) or len(data["content"]) > 4000:
            raise web.HTTPBadRequest(text="Ungültige Notiz")
        try:
            result = await asyncio.to_thread(memory.store().remember, data["content"], category=str(data.get("category", "note")), source="user")
        except ValueError:
            raise web.HTTPBadRequest(text="Notiz ungültig oder enthält vertrauliche Zugangsdaten")
        return web.json_response({"ok": True, "fact": result})

    async def memory_delete(self, request):
        from . import memory
        ident = request.match_info["id"]
        if not _ID.fullmatch(ident):
            raise web.HTTPBadRequest(text="Ungültige Notiz-ID")
        result = await asyncio.to_thread(memory.store().forget, ident)
        return web.json_response({"ok": bool(result)})

    async def transcribe(self, request):
        import io
        import wave
        received = time.monotonic()
        self.stt_active_until = time.monotonic() + 20  # Gespräch läuft: Aktivierung pausiert
        if self.speech_slots.locked():
            raise web.HTTPTooManyRequests(text="Lokale Spracherkennung ist ausgelastet")
        reader = await request.multipart()
        capture = None
        felder: dict[str, str] = {}  # optionale Angaben für den Sprach-Türsteher (pruefen, kontext, jarvis_spricht)
        count = 0
        while part := await reader.next():
            count += 1
            if count > 8:
                raise web.HTTPBadRequest(text="Zu viele Multipart-Felder")
            chunks, total = [], 0
            while chunk := await part.read_chunk():
                total += len(chunk)
                # bis 60 s Sprache; der Kontext (was Jarvis sagt) darf länger sein als die übrigen Felder
                maximum = 1_920_044 if part.name == "file" else 4096 if part.name == "kontext" else 1024
                if total > maximum:
                    raise web.HTTPRequestEntityTooLarge(max_size=maximum, actual_size=total)
                chunks.append(chunk)
            if part.name == "file":
                if capture is not None:
                    raise web.HTTPBadRequest(text="Nur eine Aufnahme erlaubt")
                capture = b"".join(chunks)
            elif part.name in ("pruefen", "kontext", "jarvis_spricht"):
                felder[part.name] = b"".join(chunks).decode("utf-8", "replace")
        if not capture:
            raise web.HTTPBadRequest(text="WAV-Aufnahme fehlt")
        try:
            with wave.open(io.BytesIO(capture), "rb") as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
                    raise ValueError()
                if not 3200 <= wav.getnframes() <= 960000:
                    raise ValueError()
                pcm = wav.readframes(wav.getnframes())
        except (wave.Error, EOFError, ValueError):
            raise web.HTTPBadRequest(text="16-kHz-Mono-PCM16-WAV erforderlich")
        if self.demo:
            return web.json_response({"text": "[Demo-Audio empfangen – nicht transkribiert]"})
        await self.speech_slots.acquire()
        timing = {}
        def decode():
            timing["start"] = time.monotonic()
            import numpy as np
            from faster_whisper import WhisperModel
            from . import sprache
            from .ears import sprach_text, vocabulary_prompt

            def modell(name: str):  # je Modellname einmal laden
                cache = self.whisper_models
                if name not in cache:
                    cache[name] = WhisperModel(name, device="cpu", compute_type="int8",
                                               cpu_threads=max(1, min(os.cpu_count() or 2, 4)), num_workers=1)
                return cache[name]
            audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
            deutsch_modell = sprache.whisper_standard()
            sprache_code = sprache.whisper_sprache()
            if sprache_code is None:  # Automatisch: zwischen Deutsch, Englisch und Arabisch wählen
                sprache_code = sprache.waehle_mehrsprachig(modell(deutsch_modell).detect_language(audio)[2])
            arabisch = sprache_code == "ar"
            # Arabisch: genaueres Modell und ägyptischer Vorgabetext; de/en bleiben schnell wie bisher.
            segments, info = modell(sprache.whisper_modell(deutsch_modell, sprache_code)).transcribe(
                audio, language=sprache_code, beam_size=3 if arabisch else 1, vad_filter=True,
                condition_on_previous_text=False,
                initial_prompt=sprache.whisper_vorgabe(vocabulary_prompt(), sprache_code))
            timing["sprache"] = sprache_code
            text = sprach_text(segments)[:8000]  # geratene Segmente (Geräusch, Echo-Rest) fallen weg
            timing["end"] = time.monotonic()
            return text
        future = asyncio.get_running_loop().run_in_executor(self.speech_pool, decode)
        future.add_done_callback(lambda _: self.speech_slots.release())
        try:
            text = await asyncio.shield(future)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise web.HTTPServiceUnavailable(text="Lokales Whisper-Modell nicht verfügbar; Installation und Cache prüfen")
        # Gemessene Zeiten für die Regie-Kette: Warten in der Bridge (Upload, Prüfung, Slot) und reines Dekodieren.
        start, end = timing.get("start", received), timing.get("end", time.monotonic())
        body = {"text": text, "sprache": timing.get("sprache"),
                "bridge_wait_ms": round(max(0.0, start - received) * 1000, 1),
                "decode_ms": round(max(0.0, end - start) * 1000, 1)}
        if felder.get("pruefen") == "1":
            # Sprach-Türsteher (TypeSafe): antworten, nur aufhören oder ignorieren. Außerhalb des Whisper-Slots.
            from . import sprachpruefung
            spricht = felder.get("jarvis_spricht") == "1"
            try:  # Gesamtgrenze: die Antwort wartet höchstens so lange auf den Türsteher
                urteil = await asyncio.wait_for(asyncio.to_thread(
                    sprachpruefung.pruefe, text, felder.get("kontext", "")[:2000], spricht), sprachpruefung.ZEITLIMIT_S + 0.5)
            except asyncio.TimeoutError:
                urteil = {"aktion": sprachpruefung.ANTWORTEN, "grund": "typesafe-fehler: Zeitlimit", "quelle": "fehler", "ms": None}
            body.update({"aktion": urteil["aktion"], "grund": urteil["grund"], "pruef_quelle": urteil["quelle"],
                         "pruef_ms": urteil["ms"]})
        return web.json_response(body)

    async def synthesize(self, request):
        from . import tts
        data = await self.json_body(request)
        text = data.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise web.HTTPBadRequest(text="Ungültiger TTS-Satz")
        # Kurz warten statt sofort abzulehnen: nach einer Unterbrechung laufen alte Vertonungen noch aus.
        try:
            await asyncio.wait_for(self.tts_slots.acquire(), timeout=TTS_SLOT_WAIT)
        except asyncio.TimeoutError:
            raise web.HTTPTooManyRequests(text="Sprachsynthese ist ausgelastet")
        future = asyncio.get_running_loop().run_in_executor(self.tts_pool, tts.synthesize, text)
        future.add_done_callback(lambda _: self.tts_slots.release())
        try:
            audio = await asyncio.shield(future)
        except asyncio.CancelledError:
            raise
        except Exception:
            raise web.HTTPServiceUnavailable(text="Sprachsynthese nicht verfügbar; lokale Konfiguration prüfen")
        if not audio or len(audio) > 2_000_000:
            raise web.HTTPBadGateway(text="Ungültiges TTS-Audio")
        return web.Response(body=audio, content_type="audio/mpeg", headers={"Cache-Control": "no-store"})

    async def synthesize_stream(self, request):
        """Satz-Vertonung als Strom: PCM-Stücke gehen raus, sobald ElevenLabs sie liefert.
        Begrenzter Puffer (Gegendruck), Zeitlimit, Abbruch beim Auflegen schließt auch die Anbieter-Verbindung."""
        import queue as queue_mod
        from . import events, tts
        data = await self.json_body(request)
        text = data.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise web.HTTPBadRequest(text="Ungültiger TTS-Satz")
        if self.demo:
            return await self.synthesize(request)
        try:
            await asyncio.wait_for(self.tts_slots.acquire(), timeout=TTS_SLOT_WAIT)
        except asyncio.TimeoutError:
            raise web.HTTPTooManyRequests(text="Sprachsynthese ist ausgelastet")
        cancel = threading.Event()
        self.tts_active_until = max(self.tts_active_until, time.monotonic() + 30)
        chunks: queue_mod.Queue = queue_mod.Queue(maxsize=8)  # ~1,6 s Ton: Gegendruck statt Speicherwachstum
        started = time.monotonic()
        meta: dict = {}
        DONE = object()

        def produce():
            try:
                mime, parts = tts.stream(text, cancel=cancel)
                meta["mime"] = mime
                chunks.put(("mime", mime))
                for part in parts:
                    while not cancel.is_set():
                        try:
                            chunks.put(("data", part), timeout=0.5)
                            break
                        except queue_mod.Full:
                            continue
                    if cancel.is_set():
                        close = getattr(parts, "close", None)
                        if close:
                            close()
                        return
            except Exception as exc:
                chunks.put(("error", exc))
            finally:
                chunks.put((DONE, None))

        future = asyncio.get_running_loop().run_in_executor(self.tts_pool, produce)
        future.add_done_callback(lambda _: self.tts_slots.release())
        response = None
        first_ms = None
        total = 0
        deadline = time.monotonic() + 45
        try:
            while True:
                if time.monotonic() > deadline:
                    raise web.HTTPGatewayTimeout(text="Sprachsynthese dauert zu lange")
                try:
                    kind, value = await asyncio.to_thread(chunks.get, True, 15)
                except queue_mod.Empty:
                    raise web.HTTPGatewayTimeout(text="Sprachsynthese antwortet nicht")
                if kind is DONE:
                    break
                if kind == "error":
                    if response is None:
                        raise web.HTTPServiceUnavailable(text="Sprachsynthese nicht verfügbar")
                    break
                if kind == "mime":
                    response = web.StreamResponse(headers={"Content-Type": "application/octet-stream",
                                                           "X-Audio-Mime": value, "Cache-Control": "no-store"})
                    await response.prepare(request)
                    continue
                if first_ms is None:
                    first_ms = round((time.monotonic() - started) * 1000)
                total += len(value)
                await response.write(value)
        except (ConnectionResetError, asyncio.CancelledError):
            cancel.set()  # Java hat aufgelegt (STOP/Barge-in): Anbieter-Stream schließen
            raise
        except BaseException:
            cancel.set()
            raise
        # Ton läuft im Browser noch so lange, wie er dauert (+ Reserve): so lange gilt "spricht".
        seconds = total / (2 * tts.PCM_RATE) if str(meta.get("mime", "")).startswith("audio/pcm") else 3.0
        self.tts_active_until = time.monotonic() + seconds + 1.5
        events.log("tts_stream", format=meta.get("mime", "?"), erster_chunk_ms=first_ms,
                   gesamt_ms=round((time.monotonic() - started) * 1000), bytes=total)
        if response is None:
            raise web.HTTPBadGateway(text="Kein TTS-Audio")
        await response.write_eof()
        return response

    async def close(self, app):
        for state in list(self.requests.values()):
            self.cancel(state)
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.cancel_pool.shutdown(wait=False, cancel_futures=True)
        self.speech_pool.shutdown(wait=False, cancel_futures=True)
        self.tts_pool.shutdown(wait=False, cancel_futures=True)


def create_app(token: str | None = None, demo: bool | None = None) -> web.Application:
    bridge = Bridge(token if token is not None else os.getenv("JARVIS_BRIDGE_TOKEN", ""), demo)

    @web.middleware
    async def auth(request, handler):
        socket = request.transport.get_extra_info("sockname") if request.transport else None
        port = socket[1] if socket else 8766
        if request.remote not in {"127.0.0.1", "::1"} or request.host not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            raise web.HTTPForbidden(text="Nur lokale Brücke")
        if request.headers.get("Origin") is not None:
            raise web.HTTPForbidden(text="Kein direkter Browserzugriff")
        if request.path == "/health":
            return await handler(request)
        if request.path == "/internal/n8n/callback":
            return await handler(request)  # eigene Prüfung: HMAC-Signatur des Workflows (nur Loopback, s. o.)
        supplied = request.headers.get("Authorization", "")
        if not hmac.compare_digest(supplied.encode(), ("Bearer " + bridge.token).encode()):
            raise web.HTTPUnauthorized(text="Authentifizierung erforderlich")
        try:
            return await handler(request)
        except (ValueError, TypeError, json.JSONDecodeError):
            raise web.HTTPBadRequest(text="Ungültige Anfrage")

    app = web.Application(middlewares=[auth], client_max_size=1_000_000)
    app[web.AppKey("bridge", Bridge)] = bridge
    async def health(request):
        # "audio": grobe Sprechaktivität für die akustische Aktivierung (gibt in dieser Zeit das Mikrofon frei).
        now = time.monotonic()
        return web.json_response({"ok": True, "demo": bridge.demo,
                                  "audio": {"tts_aktiv": bridge.tts_active_until > now,
                                            "stt_aktiv": bridge.stt_active_until > now}})
    app.router.add_get("/health", health)
    app.router.add_post("/internal/generate", bridge.generate)
    app.router.add_post("/internal/cancel", bridge.cancel_request)
    app.router.add_post("/internal/anhaenge", bridge.anhang_hochladen)
    app.router.add_get("/internal/anhaenge", bridge.anhaenge_liste)
    app.router.add_delete("/internal/anhaenge/{id}", bridge.anhang_entfernen)
    app.router.add_get("/internal/memory", bridge.memory_get)
    app.router.add_get("/internal/news", bridge.news)
    app.router.add_get("/internal/market", bridge.market)
    app.router.add_post("/internal/memory", bridge.memory_add)
    app.router.add_delete("/internal/memory/{id}", bridge.memory_delete)
    app.router.add_get("/internal/orders", bridge.orders_list)
    app.router.add_post("/internal/orders", bridge.orders_create)
    app.router.add_patch("/internal/orders/{id}", bridge.orders_update)
    app.router.add_get("/internal/calendar", bridge.calendar)
    app.router.add_get("/internal/approvals", bridge.approvals_list)
    app.router.add_post("/internal/approvals/{id}/{aktion}", bridge.approval_decide)
    app.router.add_get("/internal/jobs", bridge.jobs_list)
    app.router.add_get("/internal/automations", bridge.automations)
    app.router.add_get("/internal/agenten", bridge.agenten)
    app.router.add_get("/internal/verbindungen", bridge.verbindungen)
    app.router.add_get("/internal/gedaechtnis", bridge.gedaechtnis)
    app.router.add_get("/internal/gedaechtnis/export", bridge.gedaechtnis_export)
    app.router.add_get("/internal/gedaechtnis/verlauf/{id}", bridge.gedaechtnis_verlauf)
    app.router.add_post("/internal/gedaechtnis/{aktion}", bridge.gedaechtnis_aktion)
    app.router.add_post("/internal/transkript", bridge.transkript)
    app.router.add_get("/internal/geocode", bridge.geocode)
    app.router.add_post("/internal/n8n/callback", bridge.n8n_callback)
    app.router.add_get("/internal/settings", bridge.settings_get)
    app.router.add_get("/internal/status", bridge.settings_get)
    app.router.add_post("/internal/settings", bridge.settings_save)
    app.router.add_post("/internal/eleven/session", bridge.eleven_session)
    app.router.add_post("/internal/tool", bridge.realtime_tool)
    app.router.add_post("/internal/jobs/extern", bridge.jobs_extern)
    app.router.add_post("/internal/jobs/cancel", bridge.jobs_cancel)
    app.router.add_post("/internal/jobs/stop-all", bridge.jobs_stop_all)
    app.router.add_post("/internal/relevanz", bridge.relevanz)
    app.router.add_post("/internal/reset", bridge.reset)
    app.router.add_post("/internal/tts/preview", bridge.tts_preview)
    app.router.add_get("/internal/logs/{art}", bridge.logs)
    app.router.add_get("/internal/events", bridge.events_poll)
    app.router.add_get("/internal/anrufe", bridge.anrufe_uebersicht)
    app.router.add_get("/internal/leads", bridge.leads_uebersicht)
    app.router.add_get("/internal/leads/session", bridge.leads_session)
    app.router.add_post("/internal/leads", bridge.lead_anlegen)
    app.router.add_post("/internal/leads/import", bridge.leads_import)
    app.router.add_get("/internal/leads/{id}", bridge.lead_detail)
    app.router.add_patch("/internal/leads/{id}", bridge.lead_aendern)
    app.router.add_post("/internal/leads/{id}/ergebnis", bridge.lead_ergebnis)
    app.router.add_post("/internal/leads/{id}/notiz", bridge.lead_notiz)
    app.router.add_get("/internal/content", bridge.content_uebersicht)
    app.router.add_post("/internal/content/status", bridge.content_status)
    app.router.add_get("/internal/faehigkeiten", bridge.faehigkeiten_list)
    app.router.add_post("/internal/faehigkeiten/starten", bridge.faehigkeit_starten)
    app.router.add_post("/internal/faehigkeiten/{id}/{aktion}", bridge.faehigkeit_aktion)
    app.router.add_post("/internal/skills/aktivieren", bridge.skill_aktivieren)
    app.router.add_post("/internal/transcribe", bridge.transcribe)
    app.router.add_post("/internal/tts", bridge.synthesize)
    app.router.add_post("/internal/tts/stream", bridge.synthesize_stream)
    if os.getenv("JARVIS_NATIVE_UI", "false").lower() in {"true", "1"}:
        # Ein Python-Prozess: native Freigabe-Karten sehen auch Aufträge aus dem Java-Dashboard.
        from http.server import ThreadingHTTPServer
        from . import webui
        native_key = web.AppKey("native_server", ThreadingHTTPServer)
        async def start_native(application):
            server = ThreadingHTTPServer(("127.0.0.1", webui.PORT), webui.Handler)
            server.daemon_threads = True
            application[native_key] = server
            threading.Thread(target=server.serve_forever, name="jarvis-native-ui", daemon=True).start()
        async def stop_native(application):
            server = application.get(native_key)
            if server:
                await asyncio.to_thread(server.shutdown)
                server.server_close()
        app.on_startup.append(start_native)
        app.on_cleanup.append(stop_native)
    if not bridge.demo:
        from . import coach, scheduler  # noqa: F401 (coach registriert die Ablaeufe)

        async def start_scheduler(application):
            scheduler.start_loop()  # die EINZIGE Scheduler-Schleife des Prozesses

        async def stop_scheduler(application):
            scheduler.stop_loop()
        app.on_startup.append(start_scheduler)
        app.on_cleanup.append(stop_scheduler)
    app.on_cleanup.append(bridge.close)
    return app


def _prewarm() -> None:
    """Prompt-Cache für das Web-Gespräch vorwärmen, damit schon die erste Frage nach dem Start ihn trifft."""
    try:
        from .agent import Agent

        Agent(channel="web", model=config.get("model") or "haiku", conversation_id="cache-vorwaermen").prewarm()
    except Exception as exc:  # nie den Start gefährden
        print(f"[bridge] Cache vorwärmen fehlgeschlagen: {exc}")


def main():
    if not _demo():
        key = config.get("anthropic_key")
        if not key:
            raise SystemExit("ANTHROPIC_API_KEY fehlt; alternativ JARVIS_DEMO=true verwenden")
        if not os.getenv("ANTHROPIC_API_KEY"):
            os.environ["ANTHROPIC_API_KEY"] = key
    from . import persist

    persist.restore()  # einmal pro Prozess; die native Oberflaeche im selben Prozess nutzt denselben Stand
    if not _demo() and config.get("cache_vorwaermen", "true").lower() != "false":
        threading.Thread(target=_prewarm, name="jarvis-cache-warm", daemon=True).start()
    web.run_app(create_app(), host="127.0.0.1", port=int(os.getenv("JARVIS_BRIDGE_PORT", "8766")), print=None)


if __name__ == "__main__":
    main()
