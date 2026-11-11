"""Verbindungen und Einrichtung: der tatsächliche Zustand jedes Dienstes – ohne Zugangsdaten auszugeben.

Zustände: verbunden · unvollstaendig (angemeldet, aber Rechte fehlen) · abgelaufen · nicht_eingerichtet · fehler.
`pruefung` sagt, worauf die Angabe beruht: "live" (der Dienst wurde gerade gefragt) oder "konfiguration"
(es wurde nur geprüft, ob ein Schlüssel hinterlegt ist – das beweist nicht, dass er funktioniert).
Live-Prüfungen sind rein lesend, kurz zwischengespeichert und senden nie mehr als das eigene Token an den Anbieter.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import config

_TTL = 300.0
_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()

GOOGLE_DIENSTE = {
    "gmail": ("Gmail (lesen, Entwürfe)", {"https://www.googleapis.com/auth/gmail.modify", "https://mail.google.com/"}),
    "kalender": ("Kalender", {"https://www.googleapis.com/auth/calendar"}),
    "drive": ("Drive (lesen)", {"https://www.googleapis.com/auth/drive.readonly", "https://www.googleapis.com/auth/drive"}),
    "sheets": ("Sheets (lesen, eigene Tabellen bearbeiten)", {"https://www.googleapis.com/auth/drive.file"}),
    "aufgaben": ("Aufgaben", {"https://www.googleapis.com/auth/tasks"}),
    "kontakte": ("Kontakte (lesen)", {"https://www.googleapis.com/auth/contacts.readonly"}),
}
_GOOGLE_NEU = "Im Terminal: python -m jarvis --google-verbinden – bei Google ALLE Häkchen setzen."


def _cached(key: str, fn, force: bool = False) -> dict:
    with _lock:
        hit = _cache.get(key)
    if hit and not force and time.monotonic() - hit[0] < _TTL:
        return hit[1]
    value = fn()
    value["geprueft"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with _lock:
        _cache[key] = (time.monotonic(), value)
    return value


def _item(ident: str, name: str, zweck: str, zustand: str, pruefung: str, *, details: list[str] | None = None,
          schritte: list[str] | None = None, hinweis: str = "", dienste: dict | None = None) -> dict:
    return {"id": ident, "name": name, "zweck": zweck, "zustand": zustand, "pruefung": pruefung,
            "details": details or [], "schritte": schritte or [], "hinweis": hinweis, "dienste": dienste or {}}


def google_status(force: bool = False) -> dict:
    """Fragt Google, welche Rechte das hinterlegte Token WIRKLICH hat (tokeninfo, nur lesend)."""
    from .tools import google

    base = ("google", "Google", "Gmail-Entwürfe, Kalender, Drive, Aufgaben, Kontakte")
    if not google.is_connected():
        return _item(*base, "nicht_eingerichtet", "konfiguration", schritte=[
            "Google Cloud Console: OAuth-Client vom Typ „Desktop-App“ anlegen, JSON herunterladen.",
            "Im Terminal: python -m jarvis --google-verbinden pfad/zur/client_secret.json"])

    def check() -> dict:
        import requests
        from google.auth.exceptions import RefreshError
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        path = google.token_path()
        try:
            creds = Credentials.from_authorized_user_file(str(path))
            if not creds.valid:
                if not creds.refresh_token:
                    return _item(*base, "abgelaufen", "live", hinweis="Anmeldung abgelaufen, kein Erneuerungs-Token.",
                                 schritte=[_GOOGLE_NEU])
                creds.refresh(Request())
                path.write_text(creds.to_json(), encoding="utf-8")
                path.chmod(0o600)
            # Token im Formular-Body, nicht in der URL (taucht so in keinem Proxy-/Server-Log auf).
            r = requests.post("https://oauth2.googleapis.com/tokeninfo", data={"access_token": creds.token}, timeout=10)
        except RefreshError:
            return _item(*base, "abgelaufen", "live", hinweis="Google hat die Anmeldung beendet oder widerrufen.",
                         schritte=[_GOOGLE_NEU])
        except Exception as exc:
            return _item(*base, "fehler", "live", hinweis=f"Google nicht erreichbar ({type(exc).__name__}).")
        if r.status_code != 200:
            return _item(*base, "abgelaufen", "live", hinweis=f"Google lehnt das Token ab ({r.status_code}).",
                         schritte=[_GOOGLE_NEU])
        granted = set(str(r.json().get("scope", "")).split())
        dienste = {key: {"name": label, "aktiv": bool(scopes & granted)} for key, (label, scopes) in GOOGLE_DIENSTE.items()}
        fehlend = [d["name"] for d in dienste.values() if not d["aktiv"]]
        aktiv = [d["name"] for d in dienste.values() if d["aktiv"]]
        if fehlend:
            return _item(*base, "unvollstaendig", "live", dienste=dienste,
                         details=[f"Erteilt: {', '.join(aktiv) or 'nichts'}", f"Fehlt: {', '.join(fehlend)}"],
                         hinweis="Angemeldet, aber nicht alle Berechtigungen erteilt.", schritte=[_GOOGLE_NEU])
        return _item(*base, "verbunden", "live", dienste=dienste,
                     details=[f"Erteilt: {', '.join(aktiv)}", "Gmail: Jarvis erstellt Entwürfe; Senden nur nach deiner Freigabe.",
                              "Fremde Tabellen bearbeiten: " + ("erlaubt (nur mit Freigabe)" if {
                                  "https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"} & granted
                                  else "nicht erteilt (optional, beim Verbinden das Sheets-Häkchen setzen)")])

    return _cached("google", check, force)


def eleven_status(force: bool = False) -> dict:
    """Prüft den ElevenLabs-Schlüssel live: Konto erreichbar? Berechtigung „ElevenLabs Agents“ (Echtzeit) erteilt?"""
    base = ("elevenlabs", "ElevenLabs", "Stimme (TTS) und Echtzeit-Sprachgespräch")
    key = config.get("elevenlabs_key")
    if not key:
        return _item(*base, "nicht_eingerichtet", "konfiguration", hinweis="Ohne Schlüssel spricht die kostenlose Microsoft-Stimme.",
                     schritte=["elevenlabs.io → Settings → API Keys: Schlüssel mit „Text to Speech“ und „ElevenLabs Agents“ (Schreiben) erstellen.",
                               "Lokal als Umgebungsvariable ELEVENLABS_API_KEY hinterlegen (nicht in den Chat kopieren), dann ./jarvis-start.sh neustart"])

    def check() -> dict:
        import requests

        def probe(path: str) -> tuple[int, str]:
            """HTTP-Status und – bei Ablehnung – die von ElevenLabs genannte fehlende Berechtigung (nie der Schlüssel)."""
            try:
                r = requests.get("https://api.elevenlabs.io/v1" + path, headers={"xi-api-key": key}, timeout=10)
            except Exception:
                return 0, ""
            reason = ""
            if r.status_code >= 400:
                try:
                    detail = r.json().get("detail") or {}
                    reason = str(detail.get("status") or "") if isinstance(detail, dict) else ""
                    if reason == "missing_permissions":
                        import re as _re
                        m = _re.search(r"permission (\w+)", str(detail.get("message") or ""))
                        reason = "fehlt: " + (m.group(1) if m else "Berechtigung")
                except ValueError:
                    pass
            return r.status_code, reason

        (konto, k_grund), (stimmen, s_grund), (agents, a_grund) = probe("/user"), probe("/voices?page_size=1"), probe("/convai/agents?page_size=1")
        dienste = {"konto": {"name": "Schlüssel gültig (Konto lesbar)", "aktiv": konto == 200},
                   "stimmen": {"name": "Stimmen lesen (voices_read)", "aktiv": stimmen == 200},
                   "echtzeit": {"name": "Echtzeit-Gespräch (ElevenLabs Agents, convai)", "aktiv": agents == 200}}
        details = [f"Konto: HTTP {konto or 'keine Antwort'}{' · ' + k_grund if k_grund else ''}",
                   f"Stimmen: HTTP {stimmen or 'keine Antwort'}{' · ' + s_grund if s_grund else ''}",
                   f"Agents: HTTP {agents or 'keine Antwort'}{' · ' + a_grund if a_grund else ''}",
                   "Sprachausgabe (text_to_speech) wird nicht live geprüft – ein Test würde Guthaben verbrauchen."]
        fix = ["elevenlabs.io → Settings → API Keys → Schlüssel bearbeiten (oder neu erstellen).",
               "Berechtigungen: „Text to Speech“ = Zugriff, „Voices“ = Lesen, „ElevenLabs Agents“ = Schreiben (enthält Lesen).",
               "Wird ein neuer Schlüssel erstellt: lokal in ~/.zshrc bei ELEVENLABS_API_KEY ersetzen (nicht in den Chat), dann ./jarvis-start.sh neustart"]
        if not any((konto, stimmen, agents)):
            return _item(*base, "fehler", "live", details=details, hinweis="ElevenLabs nicht erreichbar.")
        if konto == 401 and not k_grund.startswith("fehlt") and stimmen == 401 and agents == 401 and not a_grund.startswith("fehlt"):
            return _item(*base, "abgelaufen", "live", dienste=dienste, details=details,
                         hinweis="ElevenLabs lehnt den Schlüssel ab (ungültig oder gelöscht).", schritte=fix)
        if agents == 200 and stimmen == 200:
            from . import eleven_agent

            details.append(f"Höchstdauer je Echtzeit-Gespräch: {eleven_agent.max_duration_seconds() // 60} min (eleven_max_minuten)")
            return _item(*base, "verbunden", "live", dienste=dienste, details=details)
        fehlt = [g.replace("fehlt: ", "") for g in (s_grund, a_grund) if g.startswith("fehlt")]
        return _item(*base, "unvollstaendig", "live", dienste=dienste, details=details,
                     hinweis=("Der Schlüssel ist gültig, aber ihm fehlen Berechtigungen"
                              + (f" ({', '.join(fehlt)})" if fehlt else "") + ". "
                              + ("Ohne Agents-Berechtigung startet das Echtzeit-Gespräch nicht (Fehler 401)." if agents != 200 else "")),
                     schritte=fix)

    return _cached("elevenlabs", check, force)


def _claude_code_mcp() -> dict:
    """Ist der Jarvis-MCP-Server in Claude Code für dieses Projekt registriert? (Nur Namen und Befehl, keine Umgebung.)"""
    root = str(Path(__file__).resolve().parent.parent)
    befehl = f"claude mcp add jarvis -- {Path.home()}/.jarvis/venvs/v3/bin/python -m jarvis.mcp_server"
    base = ("claude_code", "Claude Code → Jarvis (MCP, lokal)", "Claude Code liest Jarvis-Daten und speichert/sucht Erinnerungen")
    try:
        data = json.loads((Path.home() / ".claude.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _item(*base, "nicht_eingerichtet", "konfiguration", schritte=[f"Im Projektordner: {befehl}"])
    servers = ((data.get("projects") or {}).get(root) or {}).get("mcpServers") or {}
    names = [n for n, v in servers.items() if isinstance(v, dict) and "jarvis.mcp_server" in " ".join(map(str, v.get("args") or []))]
    last = ""
    try:
        lines = (config.data_dir() / "mcp-zugriffe.jsonl").read_text(encoding="utf-8").strip().splitlines()
        if lines:
            entry = json.loads(lines[-1])
            last = f"Letzter Zugriff: {entry.get('zeit', '?')} · {entry.get('werkzeug', '?')}" + (f" · {entry['client']}" if entry.get("client") else "")
    except (OSError, ValueError):
        pass
    if not names:
        return _item(*base, "nicht_eingerichtet", "konfiguration", schritte=[f"Im Projektordner: {befehl}"])
    return _item(*base, "verbunden", "konfiguration",
                 details=[f"Registriert als „{names[0]}“ (stdio, nur dieser Mac)", last or "Noch kein Zugriff protokolliert.",
                          "Werkzeuge: lesen (Anrufe, Content, Gedächtnis) und Gedächtnis speichern/löschen (nur eigene MCP-Einträge)."],
                 hinweis="Gilt nur für Claude Code auf diesem Rechner – nicht für claude.ai im Browser.")


def uebersicht(force: bool = False) -> dict:
    from . import github_quelle, mcp_client, uebersicht as status_mod
    from .tools import hubspot, telegram

    from . import browser, modelle

    items: list[dict] = []
    current = modelle.current()
    for provider in modelle.PROVIDERS.values():
        has_key = (not provider.needs_key) or bool(modelle.api_key(provider))
        active = provider.id == current.provider
        reason = modelle.problem(current) if active else None
        zweck = ("Gehirn – aktuell gewählt: " + current.model) if active else (
            "Antworten, Agenten, Recherche (Claude-Werkzeuge)" if provider.id == "anthropic" else "Alternatives Gehirn")
        steps = {"anthropic": ["console.anthropic.com → API Keys; lokal als ANTHROPIC_API_KEY hinterlegen."],
                 "moonshot": ["platform.kimi.ai → API Keys; lokal als MOONSHOT_API_KEY hinterlegen, Jarvis neu starten."],
                 "ollama": ["Ollama-App starten, Modell laden (ollama pull <modell>), JARVIS_OLLAMA_MODEL=<modell> setzen."]}[provider.id]
        if provider.id == "ollama":  # lokal: wirklich nachsehen, ob Ollama läuft und das Modell da ist
            choice = modelle.catalog()["ollama"]
            row = next(r for r in modelle.overview() if r["key"] == "ollama")
            reason = row["problem"] if (active or choice.model) else None
            has_key = bool(choice.model) and row["verfuegbar"]
        state = ("verbunden" if has_key and not reason else "unvollstaendig" if active or reason else "nicht_eingerichtet")
        items.append(_item(provider.id, provider.name, zweck, state, "konfiguration",
                           details=([f"Schlüssel hinterlegt (nicht live geprüft)."] if provider.needs_key and has_key else [])
                           + ([provider.hinweis] if provider.hinweis else []),
                           hinweis=reason or "", schritte=[] if state == "verbunden" else steps))
    bstatus = browser.status()
    items.append(_item("browser", "Browser (browser-harness)", "Echten Chrome steuern – Klicken/Tippen nur nach Freigabe",
                       bstatus["zustand"], "konfiguration", hinweis=bstatus["hinweis"], schritte=bstatus["schritte"]))
    for ident, name, fn in (("google", "Google", google_status), ("elevenlabs", "ElevenLabs", eleven_status)):
        try:
            items.append(fn(force))
        except Exception as exc:  # eine Prüfung darf die Seite nie sprengen
            items.append(_item(ident, name, "", "fehler", "live", hinweis=f"Status nicht prüfbar ({type(exc).__name__})."))
    hub = hubspot.is_configured()
    items.append(_item("hubspot", "HubSpot CRM", "Kontakte, Deals, Notizen – Schreiben nur nach Vorschau und Freigabe",
                       "verbunden" if hub else "nicht_eingerichtet", "konfiguration",
                       details=["Token hinterlegt (nicht live geprüft)."] if hub else [],
                       schritte=[] if hub else [
                           "HubSpot → Einstellungen → Integrationen → Private Apps → App erstellen.",
                           "Scopes: crm.objects.contacts.read/write, crm.objects.deals.read/write, crm.objects.companies.read, sales-email-read (für Notizen/Aufgaben: crm.objects.notes/tasks, falls angeboten).",
                           "Token lokal als HUBSPOT_TOKEN hinterlegen, dann ./jarvis-start.sh neustart"]))
    gh = github_quelle.status()
    items.append(_item("github", "GitHub", "Öffentliche Repos suchen und isoliert analysieren; privat nur mit Token und Freigabe",
                       "verbunden" if gh["token"] else ("unvollstaendig" if gh["git"] else "nicht_eingerichtet"), "konfiguration",
                       details=["git vorhanden" if gh["git"] else "git fehlt",
                                "Token hinterlegt" if gh["token"] else "Kein Token: nur öffentliche Repos, niedrigeres Abfragelimit"],
                       schritte=[] if gh["token"] else ["Optional: github.com → Settings → Developer settings → Fine-grained token (nur Lesen), lokal als JARVIS_GITHUB_TOKEN."]))
    mcp = mcp_client.load_config() or {}
    items.append(_item("mcp_ausgehend", "Jarvis → weitere MCP-Server", "Zusätzliche Werkzeuge für Jarvis-Agenten",
                       "verbunden" if mcp else "nicht_eingerichtet", "konfiguration",
                       details=[f"{len(mcp)} Server in ~/.jarvis/mcp.json: {', '.join(sorted(mcp))}"] if mcp else [],
                       schritte=[] if mcp else ["Vorlage mcp.example.json nach ~/.jarvis/mcp.json kopieren und Server eintragen."]))
    items.append(_claude_code_mcp())
    items.append(_item("claude_web", "claude.ai (Web/Cloud) → Jarvis", "Erinnerungen aus Cloud-Chats in Jarvis speichern",
                       "nicht_eingerichtet", "konfiguration",
                       hinweis="Technisch nicht verbunden: claude.ai erreicht nur öffentlich erreichbare Remote-MCP-Server (HTTPS + OAuth). "
                               "Jarvis bietet bewusst nur lokales stdio an – ein offener Endpunkt ohne Anmeldung wäre unsicher.",
                       schritte=["Voraussetzung: gehosteter Remote-MCP-Server mit HTTPS und OAuth 2.1 (z. B. hinter Cloudflare Access/Tunnel).",
                                 "Danach in claude.ai unter Einstellungen → Connectors als Custom Connector eintragen."]))
    tg = False
    try:
        tg = bool(telegram.is_configured())
    except Exception:
        pass
    items.append(_item("telegram", "Telegram", "Kanäle lesen", "verbunden" if tg else "nicht_eingerichtet", "konfiguration",
                       schritte=[] if tg else ["python -m jarvis --telegram-verbinden"]))
    n8n = status_mod.n8n_workflows()
    items.append(_item("n8n", "n8n-Workflows", "Externe Automationen mit Freigabe",
                       "verbunden" if n8n["status"] == "bereit" else "fehler" if n8n["status"] == "fehler" else "nicht_eingerichtet",
                       "konfiguration", hinweis=n8n.get("hinweis", ""),
                       details=[f"{len(n8n['workflows'])} Workflows eingetragen"] if n8n["workflows"] else []))
    items.append(_item("mapbox", "Mapbox (Weltkarte)", "Dunkle Karte, Satellit, Straßen",
                       "nicht_eingerichtet" if not _mapbox_token() else "verbunden", "konfiguration",
                       details=["Token in dashboard/.env.local gefunden (wird beim Frontend-Build eingebaut)."] if _mapbox_token() else [],
                       schritte=[] if _mapbox_token() else [
                           "account.mapbox.com → Tokens: öffentliches Token (pk.…) erstellen, URL-Beschränkung auf http://localhost:8080.",
                           "In dashboard/.env.local eintragen: VITE_MAPBOX_ACCESS_TOKEN=pk.…  Dann: cd dashboard && pnpm build"]))
    order = {"abgelaufen": 0, "unvollstaendig": 1, "fehler": 2, "nicht_eingerichtet": 3, "verbunden": 4}
    return {"stand": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "verbindungen": items,
            "braucht_dich": sum(1 for i in items if i["zustand"] in ("abgelaufen", "unvollstaendig", "fehler")),
            "reihenfolge": sorted((i["id"] for i in items), key=lambda k: order[next(x["zustand"] for x in items if x["id"] == k)])}


def _mapbox_token() -> bool:
    root = Path(__file__).resolve().parent.parent / "dashboard"
    for name in (".env.local", ".env"):
        try:
            for line in (root / name).read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("VITE_MAPBOX_ACCESS_TOKEN=") and line.split("=", 1)[1].strip().startswith("pk.") \
                        and "DEIN" not in line:
                    return True
        except OSError:
            continue
    return False
