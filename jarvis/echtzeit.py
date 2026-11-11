"""Gemeinsame Funktionen für Echtzeit-Modus, Sprach-Einstellungen und Jobs.

Früher lebten sie nur in der nativen Oberfläche (webui.py, Port 8765). Jetzt nutzen das Dashboard (über die
Bridge und Java auf 8080) und die alte Oberfläche dieselben Funktionen – kein Verhalten wird dupliziert.
"""

from __future__ import annotations

import re
from typing import Callable, Iterable

from . import config


def demo() -> bool:
    return str(config.get("demo", "false")).lower() in {"true", "1", "yes"}


def eleven_available() -> bool:
    from . import eleven_agent

    return eleven_agent.available()


def eleven_problem():
    from . import eleven_agent

    return eleven_agent.auth_problem if config.get("elevenlabs_key") and not eleven_agent.available() else None


def status(model_id: str | None = None) -> dict:
    """Sprach-/Modell-Status, wie ihn die Einstellungen zeigen (nur Konfiguration, keine Zugangsdaten)."""
    from . import tts
    from .agent import MODELS
    from .tools import google, hubspot

    if demo():
        return {"google": False, "hubspot": False, "elevenlabs": False, "elevenlabs_problem": None,
                "modell": "demo/local-memory", "modell_key": "sonnet", "modelle": list(MODELS), "stimme": "demo",
                "tempo": 1.0, "stimmen": [{"id": "demo", "name": "Demo-Testton"}], "name": config.get("owner_name", "Omar"),
                "sprachkern_verfuegbar": False, "sprachkern_problem": "Expliziter Offline-Demomodus",
                "eleven_tts_model": "demo", "eleven_tts_modelle": [], "demo": True}
    from .eleven_agent import TTS_MODELS

    from . import modelle

    choice = modelle.current()
    return {"google": google.is_connected(), "hubspot": hubspot.is_configured(),
            "elevenlabs": tts.has_eleven(), "elevenlabs_problem": tts.eleven_problem,
            "modell": model_id or choice.model, "modell_key": choice.key,
            "modelle": [c["key"] for c in modelle.overview(check_ollama=False)],
            "modell_auswahl": modelle.overview(), "anbieter": modelle.PROVIDERS[choice.provider].name,
            "stimme": tts.current_voice(), "tempo": tts.speed(),
            "stimmen": tts.voices(), "name": config.get("owner_name", "Omar"),
            "sprachkern_verfuegbar": eleven_available(), "sprachkern_problem": eleven_problem(),
            "eleven_tts_model": config.get("eleven_tts_model") or "eleven_flash_v2_5", "eleven_tts_modelle": TTS_MODELS}


def save_settings(data: dict, set_model: Callable[[str], None] | None = None) -> dict:
    """Stimme, Tempo, Stimmmodell und Gehirn-Modell speichern. `set_model` stellt laufende Agenten sofort um."""
    from . import modelle, tts

    if not isinstance(data, dict):
        raise ValueError("ungültige Einstellungen")
    if "stimme" in data:
        v = str(data["stimme"])
        if v not in {x["id"] for x in tts.voices()}:
            raise ValueError("unbekannte Stimme")
        config.save("voice", v)
        if v.startswith("eleven:"):
            tts.eleven_problem = None  # neuer Versuch erlaubt
    if "tempo" in data:
        config.save("voice_speed", f"{max(0.8, min(1.3, float(data['tempo']))):.2f}")
    if "eleven_tts_model" in data:
        from .eleven_agent import TTS_MODELS

        if data["eleven_tts_model"] not in TTS_MODELS:
            raise ValueError("unbekanntes Stimmmodell")
        config.save("eleven_tts_model", data["eleven_tts_model"])
    model_id = None
    if "modell" in data:
        key = str(data["modell"])
        choice = modelle.catalog().get(key)
        if choice is None:
            raise ValueError("unbekanntes Modell")
        reason = modelle.problem(choice)
        if reason:  # nicht umstellen, wenn der Anbieter nicht eingerichtet ist (sonst wäre Jarvis danach stumm)
            raise ValueError(f"{choice.label} ist nicht eingerichtet: {reason}")
        config.save("model", key)
        model_id = choice.model
        if set_model:
            set_model(key)
    return status(model_id)


def tool_in_job(name: str, args: dict, job_id: str) -> str:
    """Werkzeug für den ElevenLabs-Echtzeitmodus – als Schritt im zugehörigen Job sichtbar."""
    from . import eleven_agent, jobs, runtime, telemetry

    job = jobs.get(job_id) if job_id else None
    if job is None or job["status"] not in (jobs.RUNNING, jobs.WAITING):
        if job is not None and job["status"] == jobs.CANCELLED:
            return "Abgebrochen: Der Nutzer hat diesen Auftrag gestoppt. Nichts weiter ausfuehren."
        return eleven_agent.run_tool(name, args)
    tool = {t.name: t for t in eleven_agent.voice_tools()}.get(name)
    external = bool(tool and "bestaetigt_vom_nutzer" in tool.properties)
    step = jobs.add_step(job, name, args, "freigabe" if external else "lesen")
    ids = {"job_id": job_id, "conversation_id": job["conversation"]}
    runtime.bus.publish({"typ": "schritt", "schritt": dict(step), **ids})
    t = telemetry.now_ms()
    out = eleven_agent.run_tool(name, args)
    if job["status"] == jobs.CANCELLED:
        jobs.finish_step(step, jobs.CANCELLED)
        out = "Abgebrochen: Der Nutzer hat diesen Auftrag gestoppt. Ergebnis verworfen, nichts weiter ausfuehren."
    elif out.startswith("VORLAGE erstellt"):
        m = re.search(r"Freigabe-ID (\w+)", out)
        if m:
            step["freigabe_id"] = m.group(1)
        jobs.finish_step(step, jobs.WAITING, out)
    elif out.startswith(("Fehler", "Unerwarteter Fehler")):
        jobs.finish_step(step, jobs.FAILED, error=out)
    else:
        jobs.finish_step(step, jobs.COMPLETED, out)
    telemetry.tool(job_id, name, telemetry.now_ms() - t, step["status"])
    runtime.bus.publish({"typ": "schritt", "schritt": dict(step), **ids})
    return out


def run_realtime_tool(name: str, args: dict, job_id: str) -> str:
    """Echtzeit-Werkzeug im Freigabe-Kontext `echtzeit`: Freigaben bleiben nur im nächsten Zug möglich."""
    from . import approvals

    if demo():
        return "Demo: Keine externen Werkzeuge oder Konten aufgerufen."
    if not isinstance(args, dict):
        args = {}
    with approvals.context("echtzeit", approvals.current_turn("echtzeit"), "echtzeit"):
        return tool_in_job(str(name)[:80], args, str(job_id or "")[:64])


def extern_job(data: dict) -> dict:
    """Jobs für den Echtzeitmodus: start / ende (Status) – die Stimme läuft bei ElevenLabs."""
    from . import eleven_agent, jobs, runtime, telemetry

    action = data.get("aktion")
    if action == "start":
        from . import approvals

        approvals.next_turn("echtzeit")  # jede Äußerung im Echtzeitmodus ist ein neuer Nutzerzug
        job = jobs.create(str(data.get("titel", "Sprachauftrag"))[:120], "echtzeit", "echtzeit")
        model = eleven_agent.LLM.get(config.get("model") or "sonnet", "")
        telemetry.start_job(job["id"], "echtzeit", "elevenlabs/" + model, "echtzeit")
        runtime.bus.publish({"typ": "job", "job": runtime._public(job), "job_id": job["id"], "conversation_id": "echtzeit"})
        return {"job": runtime._public(job)}
    job = jobs.get(str(data.get("id", "")))
    if job is None:
        return {"fehler": "unbekannter Job"}
    if action == "ende":
        status_ = data.get("status") if data.get("status") in (jobs.COMPLETED, jobs.CANCELLED, jobs.FAILED) else jobs.COMPLETED
        jobs.finish(job, status_)
        telemetry.finish_job(job["id"], job["status"], data.get("fehlerart") if status_ == jobs.FAILED else None)
        typ = {"COMPLETED": "fertig", "CANCELLED": "abgebrochen"}.get(status_, "fehler")
        runtime.bus.publish({"typ": typ, "job": runtime._public(job), "job_id": job["id"], "conversation_id": "echtzeit"})
    return {"job": runtime._public(job)}


def stop_jobs(job_ids: Iterable[str] | None = None) -> list[str]:
    """Einzelne Jobs oder (ohne IDs) alle laufenden/wartenden Jobs abbrechen."""
    from . import faehigkeiten, jobs, runtime

    if job_ids is None:
        job_ids = [j["id"] for j in jobs.recent(50) if j["status"] in (jobs.RUNNING, jobs.WAITING)]
    # Fähigkeiten-Läufe über die Registry abbrechen (setzt das Abbruchsignal des Laufs, nicht nur den Status).
    return [jid for jid in job_ids if (faehigkeiten.cancel(jid) if faehigkeiten.is_run(jid) else runtime.cancel_job(jid))]


#: Ereignistypen, die das Dashboard über /api/events bekommt (keine Gesprächsinhalte anderer Kanäle).
EVENT_TYPES = {"kamera", "job", "schritt", "fertig", "abgebrochen", "fehler", "faehigkeit"}


def events_since(after: int, timeout: float) -> dict:
    from . import runtime

    events, seq = runtime.bus.since(max(0, after), max(0.0, min(15.0, timeout)))
    return {"events": [e for e in events if e.get("typ") in EVENT_TYPES][-300:], "seq": seq}
