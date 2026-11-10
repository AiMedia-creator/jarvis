"""Echter Status von Agenten, geplanten Abläufen, n8n-Workflows und Integrationen fürs Dashboard.

Nur Fakten aus der Runtime: was nicht eingerichtet ist, heißt "nicht_eingerichtet" (mit Hinweis, was fehlt).
Keine Zugangsdaten in der Antwort – nur, ob etwas vorhanden ist.
"""

from __future__ import annotations

import json
import time

from . import config


def _ok(flag: bool) -> str:
    return "bereit" if flag else "nicht_eingerichtet"


def _safe(fn, default=False):
    try:
        return fn()
    except Exception:
        return default


def integrations() -> list[dict]:
    from . import mcp_client
    from .tools import extras, google, hubspot, mac, telegram

    def has(key: str) -> bool:
        return bool(config.get(key))

    mcp = _safe(lambda: mcp_client.load_config(), {}) or {}
    items = [
        ("anthropic", "Claude (Gehirn)", has("anthropic_key") or bool(__import__("os").getenv("ANTHROPIC_API_KEY")),
         "ANTHROPIC_API_KEY fehlt"),
        ("google", "Google (Gmail, Kalender, Drive, Aufgaben)", _safe(google.is_connected),
         "python -m jarvis --google-verbinden"),
        ("hubspot", "HubSpot CRM", _safe(hubspot.is_configured), "HUBSPOT_TOKEN fehlt"),
        ("telegram", "Telegram-Kanäle", _safe(telegram.is_configured), "python -m jarvis --telegram-verbinden"),
        ("elevenlabs", "ElevenLabs-Stimme", has("elevenlabs_key"), "ELEVENLABS_API_KEY fehlt (sonst kostenlose Stimme)"),
        ("windy", "Windy-Webcams", has("windy_api_key"), "windy_api_key in ~/.jarvis.json"),
        ("github", "GitHub-Benachrichtigungen", _safe(extras.github_configured), "JARVIS_GITHUB_TOKEN fehlt"),
        ("obsidian", "Obsidian-Notizen", _safe(mac.obsidian_configured), "obsidian_vault in ~/.jarvis.json"),
        ("mcp", "MCP-Server", bool(mcp), f"{len(mcp)} Server in ~/.jarvis/mcp.json" if mcp else "~/.jarvis/mcp.json fehlt"),
    ]
    return [{"id": i, "name": n, "status": _ok(bool(flag)), "hinweis": "" if flag else hint} for i, n, flag, hint in items]


def agents() -> list[dict]:
    from .agent import MODELS

    model_key = config.get("model", "sonnet") or "sonnet"
    brain = bool(config.get("anthropic_key") or __import__("os").getenv("ANTHROPIC_API_KEY"))
    research_model = config.get("research_model") or model_key
    return [
        {"id": "jarvis", "name": "Jarvis", "rolle": "Hauptagent – Gespräch, Werkzeuge, Freigaben",
         "modell": MODELS.get(model_key, model_key), "status": "bereit" if brain else "nicht_eingerichtet",
         "hinweis": "" if brain else "ANTHROPIC_API_KEY fehlt"},
        {"id": "gremium", "name": "Unternehmer-Fachgremium", "rolle": "Drei unabhängige Analysen (Gründer- oder SaaS-Gremium)",
         "modell": MODELS.get(config.get("strategy_model") or model_key, model_key), "status": "bereit" if brain else "nicht_eingerichtet",
         "hinweis": "Wird bei strategischen Fragen von Jarvis aufgerufen"},
        {"id": "recherche", "name": "Tiefenrecherche", "rolle": "Mehrstufige Websuche mit Quellen",
         "modell": MODELS.get(research_model, research_model), "status": "bereit" if brain else "nicht_eingerichtet",
         "hinweis": "Dauert 1–3 Minuten je Auftrag"},
        {"id": "echtzeit", "name": "Sprachkern (ElevenLabs)", "rolle": "Echtzeit-Gespräch mit Unterbrechen",
         "modell": "ElevenLabs Agents", "status": _ok(bool(config.get("elevenlabs_key"))),
         "hinweis": "" if config.get("elevenlabs_key") else "ELEVENLABS_API_KEY fehlt"},
    ]


def workflows() -> list[dict]:
    from . import coach  # noqa: F401  registriert die Abläufe
    from . import scheduler

    out = []
    for wf in scheduler.REGISTRY.values():
        cfg = scheduler.workflow_settings(wf)
        last = _safe(lambda: scheduler._rows(
            "SELECT status, planned, finished, error FROM scheduled_runs WHERE workflow=? ORDER BY planned DESC LIMIT 1",
            (wf.key,)), [])
        when = cfg.get("uhrzeit") or wf.defaults.get("uhrzeit")
        plan = f"täglich {when}" if when else ("alle {} Std.".format(cfg["alle_stunden"]) if cfg.get("alle_stunden") else "")
        out.append({
            "id": wf.key, "name": wf.title, "beschreibung": wf.description, "aktiv": bool(cfg["aktiv"]),
            "art": "Hintergrund-Agent" if wf.kind == "agent" else "Lokale Prüfung", "zeitplan": plan,
            "letzter_lauf": ({"status": last[0]["status"], "zeit": last[0]["finished"] or last[0]["planned"],
                              "fehler": (last[0]["error"] or "")[:200]} if last else None),
        })
    return out


def n8n_workflows() -> dict:
    path = config.data_dir() / "n8n.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"status": "nicht_eingerichtet", "hinweis": "~/.jarvis/n8n.json fehlt", "workflows": []}
    except (OSError, ValueError):
        return {"status": "fehler", "hinweis": "~/.jarvis/n8n.json ist nicht lesbar", "workflows": []}
    flows = (data.get("workflows") if isinstance(data, dict) else None) or {}
    items = [{"name": str(k), "beschreibung": str(v.get("beschreibung", ""))[:200],
              "freigabe": v.get("freigabe", True) is not False}
             for k, v in flows.items() if isinstance(v, dict) and v.get("url")]
    return {"status": "bereit" if items else "nicht_eingerichtet", "hinweis": "" if items else "keine Workflows eingetragen",
            "workflows": items}


def speech_facts() -> dict:
    """Was die Sprachkette wirklich nutzt (aus der Konfiguration, nichts geschätzt)."""
    from . import tts

    voice = tts.current_voice()
    names = dict(tts.EDGE_VOICES + tts.ELEVEN_VOICES)
    eleven = voice.startswith("eleven:")
    usable = eleven and tts.has_eleven() and not getattr(tts, "eleven_problem", "")
    if eleven and not usable:
        # Fallback in tts._synthesize: ohne gültigen Schlüssel spricht die kostenlose Microsoft-Stimme.
        voice = tts.DEFAULT_EDGE
    name = names.get(voice, voice.split(":", 1)[-1]).split(" – ")[0]
    return {
        "stimme": name,
        "stimme_anbieter": "ElevenLabs" if usable else "Microsoft (edge-tts)",
        "stimme_modell": config.get("elevenlabs_model", "eleven_flash_v2_5") if usable else "",
        "stimme_hinweis": getattr(tts, "eleven_problem", "") or "",
        "erkennung": f"Whisper {config.get('whisper_model', 'base')} · lokal",
    }


def automations_status() -> dict:
    from . import jobs, scheduler

    recent = _safe(lambda: jobs.recent(30), [])
    return {
        "stand": time.time(),
        "agenten": agents(),
        "ablaeufe": _safe(workflows, []),
        "ablaeufe_schleife": "läuft" if _safe(scheduler.loop_running) else "aus",
        "n8n": _safe(n8n_workflows, {"status": "fehler", "hinweis": "nicht lesbar", "workflows": []}),
        "integrationen": _safe(integrations, []),
        "sprache": _safe(speech_facts, None),
        "jobs_laufend": sum(1 for j in recent if j.get("status") in {"RUNNING", "PLANNED", "WAITING_FOR_APPROVAL"}),
    }
