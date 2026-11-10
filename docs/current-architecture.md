# Jarvis – ursprüngliche Architektur aus jarvis-de2.zip

Der folgende Basisstand bleibt als Bestandsdokumentation erhalten. Für die ausgelieferte **V3** gelten zusätzlich SQLite/WAL-Gedächtnis, authentifizierte aiohttp-Agenten-Brücke, Java-WebFlux-Audiokanäle und React/Cesium-Command-Center. Native UI und Brücke teilen einen Python-Runtime. Aktuelle Startbefehle und Änderungen: `../README.md`; Schnittstellen: `V3-CONTRACT.md`; aktueller Teststand: `BRAIN-UMBAU-2026-10-17.md`.

## Technik der ursprünglichen nativen Basis
- **Sprache:** Python 3.12 (Umgebung `~/jarvis/venv312`, erstellt mit `uv`), Oberfläche: eine HTML-Datei mit Vanilla-JS.
- **Kein Buildsystem, kein Framework.** Server: Python-Standardbibliothek (`http.server`), keine Datenbank.
- **Abhängigkeiten:** `requirements.txt` (anthropic, requests, defusedxml, google-api-python-client, google-auth-oauthlib,
  edge-tts, pygame, sounddevice, numpy, faster-whisper, telethon). Browser-SDK von ElevenLabs liegt lokal in
  `jarvis/ui/vendor/` (v1.26.0, MIT).

## Startbefehle
| Befehl | Zweck |
|---|---|
| `jarvis` (Alias) = `python -m jarvis --mikro` | Terminal, Push-to-talk mit Whisper |
| `jarvis --ui` / `jarvis-ui` | Oberfläche auf http://localhost:8765 |
| `python -m jarvis --check` | Einrichtung prüfen |
| `python -m jarvis --google-verbinden DATEI` | Google OAuth einmalig |
| `python -m jarvis --telegram-verbinden` | Telegram einmalig |

## Laufzeitstruktur
```
Browser (ui/index.html)
  ├─ Browser-Modus: Web Speech API -> POST /api/ask (NDJSON-Stream) -> Agent (Claude) -> /api/tts
  └─ Echtzeit-Modus: ElevenLabs Agents (WebSocket, Claude Sonnet als LLM bei ElevenLabs)
        └─ Client-Tools -> POST /api/tool -> eleven_agent.run_tool -> lokale Werkzeuge
webui.py (127.0.0.1:8765, Host-Check + X-Jarvis-Header)
agent.py  (Claude-Tool-Schleife, Streaming, Abbruch, Gedächtnis im Prompt)
tools/*   (google, google_suite, hubspot, news, telegram, system) + Skill-Werkzeug
```

## Laufzeit (Stand nach Reparatur 17.10. abends)
- **Streaming:** `/api/ask` liefert NDJSON-Ereignisse: `job` (sofort), `token` (jedes Textstück), `satz` (vorlesbarer
  Abschnitt; der erste darf schon am Komma enden), `werkzeug`, `schritt` (Job-Schritt mit Status), `fertig|abgebrochen|fehler`.
- **Abbruch:** `Agent.cancel()` schließt den laufenden Claude-Stream sofort, wartet nicht auf laufende Werkzeuge
  (sie laufen im Hintergrund aus, ihr Ergebnis wird verworfen) und startet keine Folgeschritte. Eine neue Anfrage
  bricht die alte automatisch ab. Auslöser: Stopp, Esc, Klick auf den Kern während er spricht/denkt, neue Eingabe,
  Reinreden, Tab schließen (`pagehide`). Aktionen nach außen sind durch Vorlagen geschützt: ohne Freigabe kein Versand.
- **Jobs (`jobs.py`):** jeder Auftrag = Job mit Schritten (id, Werkzeug, Status, Eingabe, Ergebnis, Fehler, Zeiten,
  Berechtigung `lesen|freigabe`). Status: PLANNED, RUNNING, WAITING_FOR_APPROVAL, COMPLETED, FAILED, CANCELLED. `/api/jobs`.
- **Werkzeuge:** Lese-Werkzeuge einer Runde laufen parallel (Thread-Pool, 8), Aktionen nach außen nacheinander;
  Timeout je Werkzeug (`JARVIS_TOOL_TIMEOUT`, Standard 45 s); Kurzzeit-Cache für Lesen (News 120 s, Telegram/Kalender 60 s,
  Gmail/HubSpot-Suche 30 s), geleert nach jeder freigegebenen Aktion; Gmail holt Mails im Batch; Telegram hält eine
  Dauerverbindung; Prompt-Caching für Systemprompt und Werkzeuge.
- **Telemetrie (`telemetry.py`):** nur Zeiten/IDs/Werkzeugnamen/Status in `~/.jarvis/telemetry.jsonl`; Mittelwerte unter
  `/api/telemetry` (erstes Token, erster Satz, gesamt, Werkzeugdauern). Die Oberfläche zeigt je Job „erste Worte / Stimme / gesamt“.
- **Sprachzustände:** IDLE, LISTENING, TRANSCRIBING, THINKING, SPEAKING, INTERRUPTED, WAITING_FOR_APPROVAL, ERROR.
  Sprechmodus „Freihändig“ (Reinreden) oder „Push-to-talk“ (Leertaste/Kern halten).

## Gespräche, Jobs und Ereignisse (`runtime.py`)
- Jede `conversation_id` hat einen eigenen Agenten (Verlauf, Sperre, Abbruch). `haupt` = Sprach-/Chat-Unterhaltung,
  `bg-…` = je Hintergrundjob, `echtzeit` = ElevenLabs-Aufträge.
- Jedes Ereignis trägt `job_id` und `conversation_id`. Das Frontend verwirft alles, was nicht zum angezeigten Job gehört
  (`belongs()`), und spielt nur Audio des aktuellen Jobs.
- Hintergrundjobs: `POST /api/jobs` oder Werkzeug `hintergrund_auftrag`; eigener Thread, Ergebnis in der Job-Liste.
- Ereignis-Bus: `GET /api/events?after=SEQ&timeout=S` (Long-Polling) – Status, Schritte, Ergebnisse aller Jobs.
- Abbruch pro Job: `POST /api/jobs/cancel`; Stopp-Knopf = Hauptunterhaltung; „Alle Jobs stoppen“ = `/api/stop {alle:true}`.
- ElevenLabs: jede Äußerung = Job (`/api/jobs/extern`), Werkzeuge laufen mit `job_id` als Schritte; Stopp = sofort
  stumm (`setVolume 0`), Job abbrechen, Sitzung beenden.
- Telemetrie pro Job: job, conversation, art, modell, ui_feedback_ms, erstes_token_ms, erster_satz_ms, llm_ende_ms,
  stimme_ms, gesamt_ms, runden, status, fehlerart, unterbrochen (Browser meldet UI-Werte über `/api/telemetry/client`).

## Oberfläche (Voice-Core-HUD)
- `ui/index.html` – Layout: oben Status, links Heute/Jobs/Protokoll, Mitte Voice-Core, rechts Verlauf/Freigaben, unten
  Mikrofon, Eingabe, Gespräch, Stopp.
- `ui/voicecore.js` – `VoiceState`, `VoiceCoreController` (Zustandsmaschine + geglätteter Pegel), `AudioReactiveSource`
  mit `MicSource`, `MediaOutputSource`, `FunctionSource` (ElevenLabs-Pegel), `MockSource`; Canvas-Renderer.
- `ui/app.js` – App-Logik; Zustand wird nur aus App-Fakten abgeleitet (`computeState`):
  Mock > ERROR > SPEAKING > THINKING > WAITING_FOR_APPROVAL > LISTENING > IDLE.
- Vorführmodus ohne Mikrofon: `http://localhost:8765/?mock=1`. Reduzierte Animationen und Raster in den Einstellungen.

## Module
| Datei | Inhalt |
|---|---|
| `agent.py` | Systemprompt, Kanäle (stimme/web/text), Tool-Schleife, `cancel()`, Gedächtnis-Vorladen |
| `webui.py` | HTTP-Server, `/api/ask`, `/api/tts`, `/api/stop`, `/api/reset`, `/api/settings`, `/api/status`, `/api/overview`, `/api/eleven/session`, `/api/tool` |
| `eleven_agent.py` | ElevenLabs-Agent anlegen/aktualisieren, Tools synchronisieren, signierte URL, `web_suche`, `merken` |
| `tts.py`, `voice.py`, `ears.py` | Sprachausgabe (ElevenLabs/edge-tts), Wiedergabe (afplay), Mikrofon + Whisper |
| `config.py` | Einstellungen: Umgebungsvariablen > `~/.jarvis.json` |
| `skills.py` + `skills/*.md` | Markdown-Anleitungen (Briefing, Mails, HubSpot, Nachrichten) |
| `profile.py` | `~/.jarvis/profil.md` |
| `tools/__init__.py` | `Tool`-Datenklasse (typisierte Eingaben via JSON-Schema), `ToolFailure` |

## Datenablage (außerhalb des Codes)
- `~/.jarvis.json` – Einstellungen (Stimme, Modell, Agent-ID, Telegram api_id/hash), Rechte 600
- `~/.jarvis/` – `profil.md`, `memories/` (Gedächtnis), `google_token.json`, `telegram.session`, `skills/`, `audit.jsonl`
- `~/.zshrc` – API-Schlüssel als Umgebungsvariablen

## Vorhandene Funktionen
Gmail (suchen, lesen, Entwurf, senden nach Ja, archivieren), Kalender (lesen, anlegen nach Ja), Drive/Docs, Google-Aufgaben,
Google-Kontakte, HubSpot (Kontakte, Deals, Notizen lesen/schreiben, Aufgaben, Mail protokollieren, senden+protokollieren),
Nachrichten (RSS), Telegram-Kanäle (lesen, suchen), Websuche, Gedächtnis (Dateien), Skills, Seiten/Programme öffnen,
Heute-Übersicht, Stimmen-/Modellwahl, Abbrechen/Reinreden.

## Tests
`tests/` (pytest, offline, alle externen Dienste gemockt). Start: `python -m pytest -q`.
Echte Konten (Google, HubSpot, Telegram, ElevenLabs) sind durch die Tests nicht abgedeckt.
