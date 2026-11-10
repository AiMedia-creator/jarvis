# Übernahme aus OpenJarvis (08.11.2026)

Quelle: https://github.com/open-jarvis/OpenJarvis (Stanford, Apache-2.0). Übernommen wurden Ideen und Code-Teile,
die zu unserem Jarvis passen. Alles ist auf Deutsch angepasst und in die vorhandene Policy- und Freigabe-Logik
eingebunden. Code-Stand vor der Übernahme: `~/.jarvis/backups/jarvis-code-vor-openjarvis-2026-11-08.tar.gz`.

**Unverändert:** Stimme (ElevenLabs/edge-tts, `tts.py`, `voice.py`, `ears.py`, `speech.py`), Google-Kalender und
Google-Suite (`tools/google*.py`, `calendar_data.py`), Gedächtnis (`memory.py`, `~/.jarvis/memory.sqlite3`),
HubSpot, Telegram, n8n, Sprache, Persona und Oberflächen.

## Was neu ist

| Bereich | Datei | Herkunft in OpenJarvis | Was es tut |
|---|---|---|---|
| Schleifenwächter | `jarvis/schutz.py`, `agent.py` | `agents/loop_guard.py` | Erkennt je Nutzerzug identische Werkzeugaufrufe, A-B-A-B-Pingpong und zu viele Aufrufe desselben Werkzeugs. Erst ein Hinweis ans Modell, beim zweiten Mal Sperre. |
| Injection-Scanner | `jarvis/schutz.py` | `security/injection_scanner.py` | Prüft Webseiten, Mails, Telegram, Nachrichten, Drive, PDFs und MCP-Ergebnisse auf versteckte Anweisungen an die KI (englische **und deutsche** Muster, vorgetäuschte Freigaben). Treffer werden als Warnung vor das Ergebnis gesetzt. |
| Schlüssel schwärzen | `jarvis/schutz.py` | `security/credential_stripper.py`, `scanner.py` | API-Schlüssel, Tokens und private Schlüssel in Werkzeugergebnissen werden durch `[GESCHWAERZT:…]` ersetzt. Gilt auch im ElevenLabs-Sprachkern. |
| Rechner | `tools/extras.py` → `rechnen` | `tools/calculator.py` | Sicher (AST, kein `eval`), deutsches Dezimalkomma. |
| Wetter | `tools/extras.py` → `wetter` | `tools/weather.py` | Über Open-Meteo, **kein Schlüssel nötig**. Standardort: `"wetter_ort": "Berlin"` in `~/.jarvis.json`. |
| Hacker News | `tools/extras.py` → `hacker_news` | `connectors/hackernews.py` | Top-Storys. |
| GitHub | `tools/extras.py` → `github_benachrichtigungen` | `connectors/github_notifications.py` | Braucht `JARVIS_GITHUB_TOKEN` (nur Leserecht Notifications). |
| PDF lesen | `tools/extras.py` → `pdf_lesen` | `tools/pdf_tool.py` | Lokale PDFs im Benutzerordner (pypdf). |
| Apple Notizen | `tools/mac.py` → `notizen_suchen`, `notiz_lesen` | `connectors/apple_notes.py` | Liest die Notizen-Datenbank schreibgeschützt. Braucht **Festplattenvollzugriff** für Terminal/Python. |
| Obsidian | `tools/mac.py` → `obsidian_suchen`, `obsidian_lesen` | `connectors/obsidian.py` | `"obsidian_vault": "/Pfad"` in `~/.jarvis.json`. |
| Tiefenrecherche | `jarvis/research.py` → `tiefenrecherche` | `agents/deep_research.py` | Frage → 3-5 Teilfragen → parallele Websuche → Bericht mit nummerierten Quellen und Lücken. Eigenes Zeitlimit (~3 Min). |
| MCP-Client | `jarvis/mcp_client.py` | `mcp/client.py`, `transport.py` | Beliebige MCP-Server (stdio oder HTTPS) als Werkzeuge `mcp_<server>_<werkzeug>`. Schreibende MCP-Werkzeuge laufen über Vorlage + Freigabe. Beispiel: `mcp.example.json`. |
| Skill-Standard | `jarvis/skills.py` | `skills/parser.py`, `loader.py` | Neben `name.md` jetzt auch `name/SKILL.md` mit Zusatzdateien (agentskills.io – Format von OpenJarvis, Hermes, OpenClaw, Claude-Skills). |
| Skill-Import | `jarvis/skill_import.py` | `skills/importer.py`, `sources/*` | Skills aus Hermes (~150), OpenClaw, Anthropic oder jedem GitHub-Repo. **Nur Text** wird kopiert (keine Skripte), jede Datei wird gescannt, Herkunft + Commit in `.herkunft.json`. |
| 7 neue Skills | `jarvis/skills/*.md` | `skills/data/*.toml` | besprechungsnotizen, themenrecherche, aufgaben-aus-notizen, pdf-zusammenfassen, dokumente-vergleichen, wissen-extrahieren, webseite-zusammenfassen. |
| Einrichtungs-Check | `python -m jarvis --check` | `jarvis doctor` | Zeigt auch die neuen optionalen Bausteine. |

## Grenzen der Schutzschicht (Ergebnis Codex-Review)

Der Injection-Scanner ist **Frühwarnung, keine Sicherheitsgarantie**: Er erkennt bekannte Formulierungen, anders
formulierte Anweisungen kommen durch, und auch erkannter Text bleibt im Kontext (mit Warnung). Die eigentliche
Sicherung bleibt im Code: Alles, was nach außen wirkt (Mail, Termin, CRM, schreibende MCP-Werkzeuge), läuft über
Vorlage + Freigabe in `approvals.py` – im selben Nutzerzug ist keine Freigabe möglich, Hintergrundjobs können nie
freigeben. Importierte Skills gelten unabhängig vom Scan als Anleitung eines Dritten.

Codex-Review vom 08.11.2026 (review, adversarial-review, rescue-Audit) – behoben: MCP-HTTP-Adressprüfung
(`http://localhost:@fremd.example` wurde akzeptiert), Symlinks beim Skill-Import (hätten lokale Dateien einschleusen
können), halb laufender Jarvis beim Start. Geprüft und verworfen: verlorene MCP-Antworten bei parallelen Aufrufen
(Austausch ist per Sperre serialisiert), Freigabe im selben Sprachzug (jede Äußerung startet einen neuen Zug).

## Skills importieren

```bash
python -m jarvis --skill-quelle hermes          # Quelle laden (git clone --depth 1 nach ~/.jarvis/skill-cache)
python -m jarvis --skills-suchen research       # suchen
python -m jarvis --skill-installieren hermes:arxiv
python -m jarvis --skills                       # alle geladenen Skills mit Herkunft
python -m jarvis --skill-entfernen arxiv
```

Danach Jarvis neu starten (`./jarvis-start.sh neustart`). Importierte Skills stehen im Katalog mit `[importiert]`
und werden dem Modell als „Anleitung eines Dritten“ übergeben – Freigaben gelten unverändert.

## Bewusst NICHT übernommen

- **Lokale Modelle (Ollama/MLX/vLLM) und Modell-Routing:** Jarvis bleibt bei Claude. Ein lokales Modell für Deutsch,
  Stimme und Werkzeuge wäre spürbar schwächer. Möglicher Folgeschritt: opt-in Routing für einfache Fragen.
- **Lernschleife, Training, Evals, Benchmarks, Energie-Telemetrie, Rust-Kern:** Forschungsinfrastruktur ohne Nutzen
  für den täglichen Betrieb.
- **30 Messenger-Kanäle, Tauri-Desktop-App, eigenes Frontend:** Wir haben Command Center + native UI.
- **Code-Interpreter/Shell-Werkzeuge:** zu riskant für einen Geschäftsassistenten mit Zugriff auf Mail und CRM.
- **Workflow-Engine, Scheduler, Gedächtnis, Sprach-I/O, Google-Konnektoren:** vorhanden (LangGraph, n8n,
  `scheduler.py`, `memory.py`, ElevenLabs/Whisper, Google-Suite) und ausdrücklich beizubehalten.

## Tests

`tests/test_openjarvis_uebernahme.py` (41 Tests, offline, inkl. Fake-MCP-Server, Fake-Notizen-Datenbank und
lokalem Git-Repo für den Skill-Import). Gesamtstand: 291 bestanden, 3 übersprungen.
