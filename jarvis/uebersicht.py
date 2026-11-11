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
    from . import modelle

    model_key = config.get("model", "sonnet") or "sonnet"
    choice = modelle.resolve(model_key)
    main_problem = modelle.problem(choice)
    brain = bool(config.get("anthropic_key") or __import__("os").getenv("ANTHROPIC_API_KEY"))
    research_model = config.get("research_model") or model_key
    return [
        {"id": "jarvis", "name": "Jarvis", "rolle": "Hauptagent – Gespräch, Werkzeuge, Freigaben",
         "modell": f"{choice.model} ({modelle.PROVIDERS[choice.provider].name})",
         "status": "bereit" if main_problem is None else "nicht_eingerichtet", "hinweis": main_problem or ""},
        {"id": "gremium", "name": "Unternehmer-Fachgremium", "rolle": "Drei unabhängige Analysen (Gründer- oder SaaS-Gremium)",
         "modell": modelle.claude_id(config.get("strategy_model") or model_key), "status": "bereit" if brain else "nicht_eingerichtet",
         "hinweis": "Wird bei strategischen Fragen von Jarvis aufgerufen"},
        {"id": "recherche", "name": "Tiefenrecherche", "rolle": "Mehrstufige Websuche mit Quellen",
         "modell": modelle.claude_id(research_model), "status": "bereit" if brain else "nicht_eingerichtet",
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


# --- Agenten-Center: echte Jobs je Agent, Rollenabdeckung --------------------------------------------------

#: Werkzeuge, hinter denen ein eigener Unter-Agent arbeitet (eigene Modellaufrufe, eigener Auftrag).
SUB_AGENTS = {"unternehmer_strategie": ("gremium", "Unternehmer-Fachgremium"), "tiefenrecherche": ("recherche", "Tiefenrecherche")}


def agent_of(job: dict) -> dict:
    """Ordnet einen Job dem Agenten zu, der ihn tatsächlich ausführt (aus Art, Fähigkeit und Zeitplan – nichts geraten)."""
    art = job.get("art") or "vordergrund"
    saved = job.get("wiederaufnahme") if isinstance(job.get("wiederaufnahme"), dict) else {}
    if art == "faehigkeit":
        if saved.get("faehigkeit") == "skill_starten":
            name = str((saved.get("args") or {}).get("skill") or "Skill")
            return {"id": "skill:" + name, "name": f"Skill-Agent · {name}", "art": "Skill-Auftrag"}
        return {"id": "faehigkeit:" + str(saved.get("faehigkeit") or "?"), "name": "Jarvis-Fähigkeit", "art": "Fähigkeit"}
    if art == "echtzeit":
        return {"id": "echtzeit", "name": "Sprachkern (ElevenLabs)", "art": "Echtzeit-Gespräch"}
    if art == "hintergrund":
        from . import scheduler

        title = str(job.get("titel") or "")
        for wf in scheduler.REGISTRY.values():
            if title.startswith(wf.title):
                return {"id": "ablauf:" + wf.key, "name": f"Ablauf · {wf.title}", "art": "Geplanter Ablauf"}
        return {"id": "hintergrund", "name": "Hintergrund-Agent", "art": "Hintergrundauftrag"}
    return {"id": "jarvis", "name": "Jarvis (Orchestrator)", "art": "Gespräch"}


def jobs_view(limit: int = 40) -> dict:
    """Jobs fürs Agenten-Center: Agent, Schritte, Zeiten, Fortschritt aus echten Schritten, Nutzung nur wenn erfasst."""
    from . import faehigkeiten, jobs, telemetry

    laeufe = {r["id"]: r for r in _safe(lambda: faehigkeiten.recent(50), [])}
    out = []
    for job in jobs.recent(limit):
        steps = job.get("schritte") or []
        done = sum(1 for s in steps if s.get("status") in ("COMPLETED", "FAILED", "CANCELLED"))
        last = max([job.get("start") or 0, job.get("ende") or 0, *[s.get("ende") or s.get("start") or 0 for s in steps]])
        usage = _safe(lambda: telemetry.job(job["id"]), None) or {}
        tokens = {k: usage[k] for k in ("tokens_ein", "tokens_aus", "tokens_cache_lesen", "tokens_cache_schreiben") if usage.get(k)}
        lauf = laeufe.get(job["id"]) or {}
        sub = sorted({SUB_AGENTS[s["werkzeug"]][1] for s in steps if s.get("werkzeug") in SUB_AGENTS})
        waiting = [s.get("freigabe_id") for s in steps if s.get("status") == "WAITING_FOR_APPROVAL" and s.get("freigabe_id")]
        github = ((lauf.get("ergebnis") or {}).get("github") or {}) if isinstance(lauf.get("ergebnis"), dict) else {}
        out.append({
            "id": job["id"], "titel": job.get("titel") or "Auftrag", "art": job.get("art") or "vordergrund",
            "status": job.get("status"), "agent": agent_of(job), "unteragenten": sub,
            "gespraech": job.get("conversation"), "start": job.get("start"), "ende": job.get("ende"), "letzte_aktivitaet": last,
            "schritte": [{"id": s.get("id"), "werkzeug": s.get("werkzeug"), "status": s.get("status"),
                          "berechtigung": s.get("berechtigung"), "start": s.get("start"), "ende": s.get("ende"),
                          "ergebnis": s.get("ergebnis"), "fehler": s.get("fehler"), "freigabe_id": s.get("freigabe_id")}
                         for s in steps][-40:],
            "schritte_gesamt": len(steps), "schritte_fertig": done,
            "fortschritt": lauf.get("fortschritt") or "",
            "werkzeuge": sorted({str(s.get("werkzeug")) for s in steps if s.get("werkzeug")}),
            "ergebnis": job.get("ergebnis"), "fehler": job.get("fehler"),
            "unterbrochen_durch_neustart": job.get("fehler") == jobs.RESTART_REASON,
            "freigaben": waiting,
            "abbrechbar": job.get("status") in ("RUNNING", "PLANNED") and (lauf.get("abbrechbar", True) if lauf else True),
            "wiederholbar": bool(lauf.get("wiederholbar")) and job.get("status") in ("FAILED", "CANCELLED"),
            "faehigkeit": (job.get("wiederaufnahme") or {}).get("faehigkeit") if isinstance(job.get("wiederaufnahme"), dict) else None,
            "modell": usage.get("modell") or None,
            "nutzung": ({"tokens": tokens, "kosten_usd": usage.get("kosten_usd")} if tokens else None),
            "repo": ({"name": github.get("repo"), "commit": github.get("commit"), "url": github.get("url")} if github.get("repo") else None),
        })
    counts: dict[str, int] = {}
    for j in out:
        counts[j["status"]] = counts.get(j["status"], 0) + 1
    return {"jobs": out, "zahlen": counts, "stand": time.time(),
            "hinweis": "Zeigt die letzten 50 Jobs (dauerhaft in SQLite). Was bei einem Neustart lief, ist als unterbrochen markiert und läuft nicht von selbst weiter."}


#: Rollen, die Jarvis abdecken soll -> womit sie TATSÄCHLICH umgesetzt sind. Keine eigenen Schein-Agenten:
#: Eine Rolle ist der Orchestrator plus Skill (Anleitung) plus Werkzeug-Rahmen, gestartet als echter Job.
ROLLEN = [
    ("orchestrierung", "Orchestrierung", "Versteht den Auftrag, wählt Skill, Werkzeuge und Hintergrundjobs.",
     None, ["hintergrund_auftrag", "skill_starten", "auftrag_anlegen"], ""),
    ("gedaechtnis", "Gedächtnis und Wissenssuche", "Sucht und speichert belegtes Wissen; meldet Widersprüche.",
     "wissen-extrahieren", ["wissen_suchen", "wissen_speichern", "wissen_vergessen"], ""),
    ("lead_recherche", "Lead-Recherche und Qualifizierung", "Firma und Entscheider mit Quelle und Abrufzeitpunkt.",
     "lead-recherche", ["tiefenrecherche", "webseite_lesen", "auftrag_anlegen"], "Recherchiere den Lead: "),
    ("sales_coaching", "Sales- und Cold-Call-Coaching", "Gesprächseinstiege, Einwände, Rollenspiel.",
     "cold-call-vorbereitung", ["anruf_karte", "projekt_liste"], "Bereite den Anruf vor bei: "),
    ("crm_followups", "CRM und Follow-ups", "Anruf-Ergebnis, nächster Schritt, CRM-Vorlage, Mail-Entwurf.",
     "anruf-nachbereitung", ["anruf_erfassen", "anruf_crm_status", "entwurf_erstellen"], ""),
    ("content_hooks", "Content-Strategie und Hooks", "Themen, Recherche, bewertete Hook-Varianten.",
     "content-hooks", ["content_speichern", "tiefenrecherche"], "Entwickle Hooks zum Thema: "),
    ("content_skript", "Skripte und Repurposing", "Skript, Storyboard, Shot-Liste, KI-Prompts, Plattformvarianten.",
     "content-skript", ["content_liste", "content_speichern"], "Schreibe Skript und Storyboard für den Content-Eintrag: "),
    ("content_auswertung", "Content-Auswertung", "Wertet nur erfasste Kennzahlen aus, nennt Stichprobe.",
     "content-auswertung", ["content_liste", "content_ergebnis_erfassen"], "Werte meine veröffentlichten Inhalte aus."),
    ("coaching", "Business- und Lebenscoaching", "Wochenrückblick und größter Hebel aus echten Daten.",
     "wochen-coaching", ["unternehmer_strategie", "auftraege_liste", "anruf_kennzahlen"], "Mach mit mir den Wochenrückblick."),
    ("github_pruefung", "GitHub-, Skill- und Integrationsprüfung", "Repos suchen, isoliert analysieren, Skills geprüft importieren.",
     None, ["github_suchen", "github_analysieren"], ""),
]
#: Im Agenten selbst eingebaute Werkzeuge (nicht in der Werkzeug-Registry, aber immer vorhanden).
EINGEBAUT = {"wissen_suchen", "wissen_speichern", "wissen_vergessen", "hintergrund_auftrag"}


def rollen(tools: dict, skill_defs: list[dict], job_list: list[dict]) -> list[dict]:
    by_name = {s["name"]: s for s in skill_defs}
    brain = bool(config.get("anthropic_key"))
    out = []
    for ident, name, aufgabe, skill, werkzeuge, vorlage in ROLLEN:
        s = by_name.get(skill) if skill else None
        fehlend = [w for w in werkzeuge if w not in tools and w not in EINGEBAUT]
        if not brain:
            status, grund = "nicht_eingerichtet", "ANTHROPIC_API_KEY fehlt"
        elif skill and s is None:
            status, grund = "fehlt", f"Skill {skill} nicht gefunden"
        elif skill and not s["aktiviert"]:
            status, grund = "deaktiviert", s.get("sperrgrund") or "Skill deaktiviert"
        elif fehlend:
            status, grund = "teilweise", "Werkzeug fehlt: " + ", ".join(fehlend)
        else:
            status, grund = "bereit", ""
        mine = [j for j in job_list if j["agent"]["id"] == ("skill:" + skill if skill else "jarvis" if ident == "orchestrierung" else "")
                or (ident == "github_pruefung" and str(j.get("faehigkeit") or "").startswith("github_"))]
        aktiv = [j for j in mine if j["status"] in ("RUNNING", "PLANNED", "WAITING_FOR_APPROVAL")]
        out.append({"id": ident, "name": name, "aufgabe": aufgabe, "skill": skill, "werkzeuge": werkzeuge,
                    "fehlende_werkzeuge": fehlend, "status": status, "grund": grund,
                    "umsetzung": ("Hauptagent" if not skill else f"Orchestrator + Skill „{skill}“ als eigener Job"),
                    "startbar": bool(skill) and status in ("bereit", "teilweise"), "vorlage": vorlage,
                    "jobs_aktiv": len(aktiv), "jobs_gesamt": len(mine),
                    "letzter_job": ({"id": mine[0]["id"], "titel": mine[0]["titel"], "status": mine[0]["status"],
                                     "start": mine[0]["start"]} if mine else None)})
    return out
