# Jarvis V3 – zusammengeführtes Projekt

> **Neu (08.11.2026): Übernahme aus OpenJarvis.** Schleifenwächter, Injection-Scanner mit deutschen Mustern,
> Schlüssel-Schwärzung, Tiefenrecherche mit Quellen, MCP-Client, agentskills.io-Skills mit Import aus
> Hermes/OpenClaw/GitHub, neue Werkzeuge (Rechner, Wetter, Hacker News, GitHub, PDF, Apple Notizen, Obsidian) und
> sieben neue Skills. Stimme, Google-Kalender, Gedächtnis und alle Integrationen sind unverändert.
> Details: `docs/OPENJARVIS-UEBERNAHME.md`.

> **Schneller und menschlicher (08.11.2026): Sprach-Schnellspur.** Im Gespräch (Oberfläche 8765, `--stimme`) bekommt
> Jarvis einen kompakten Prompt und ~24 Kernwerkzeuge statt 7.400 + 6.200 Tokens; Fachmodule und alle Werkzeuge holt
> er sich mit `mehr_faehigkeiten`. Gemessen: erstes Wort 0,71 s statt 1,53 s. Dazu Sofort-Reaktion vor Werkzeugen
> („Moment, ich schau kurz in deinen Kalender.“), Standardpause 0,6 s, natürlicherer Sprechstil, kürzerer Verlauf.
> Abschalten: `"schnellspur": "false"` in `~/.jarvis.json`. Code: `jarvis/schnellspur.py`.

> **Live-Kameras (08.11.2026, Idee + Katalog aus [World Monitor](https://github.com/koala73/worldmonitor)).** „Zeig mir,
> was gerade in Tel Aviv los ist“ / „Berlin live“ → Jarvis prüft öffentliche Live-Webcams (YouTube-Livestreams von
> Sendern/Webcam-Betreibern), nimmt die erste, die wirklich live ist, und zeigt sie groß auf http://localhost:8765.
> „Kamera zu“ oder Esc schließt. Unbekannte Städte: Suche nach „<Stadt> live cam“ (mit `youtube_api_key` über die
> offizielle API; ohne Schlüssel über die öffentliche YouTube-Suche, abschaltbar mit `"kamera_suche": "aus"`).
> Optional weltweit mehr Kameras mit `windy_api_key` (api.windy.com). Eigene Kameras: `~/.jarvis/kameras_live.json`.
> Code: `jarvis/tools/live_kameras.py`. Keine privaten Überwachungskameras.

Dieses Paket baut direkt auf **allen 57 Dateien aus `jarvis-de2.zip`** auf. Der zuvor erstellte Java-/React-Code wurde übernommen und mit dem vorhandenen Python-Agenten verbunden. Google, HubSpot, Telegram, Skills, Jobs, Freigaben, Telemetrie und die bestehende Voice-Core-Oberfläche bleiben erhalten. Es gibt nicht zwei getrennte persönliche KIs: Das Command Center leitet Anfragen an den vorhandenen Agenten und dessen gemeinsames SQLite-Gedächtnis weiter.

## Unternehmerische Beratung mit drei Spezialisten

Bei strategischen Fragen zu Umsatz, Wachstum, Preisen, Kundengewinnung und Prioritäten kann Jarvis drei unabhängige Analysen parallel einholen: **Kunde und Markt**, **Wirtschaftlichkeit** sowie **Umsetzung und Gegenprüfung**. Das neue Werkzeug `unternehmer_strategie` ist in Text- und Sprachagent eingebunden. Die Spezialisten erhalten nur die vom Hauptagenten übergebenen Fakten und haben selbst keine Werkzeuge.

Jarvis soll verfügbare Daten aus CRM oder aktueller Recherche mit Quelle und Stand übergeben. Fehlen Zahlen, muss es Annahmen kennzeichnen und einen kleinen Test mit Messgröße und Stop-Regel vorschlagen. Der Fachrat verspricht keine Gewinne und kann keine externen Aktionen ausführen. Der vollständige, anpassbare Arbeitsstandard steht in `jarvis/skills/unternehmer-strategie.md`; die drei Rollen und Parallel-Orchestrierung in `jarvis/strategy.py`.

## Brain-Umbau (17.10.2026)

Eine Werkzeug-Prüfstelle für alle Kanäle verhindert doppelte und blind wiederholte Nebenwirkungen (Ausführungsprotokoll, „unklar“ statt Doppelversuch). Das Gedächtnis kennt Gültigkeit, Widersprüche und Löschberichte. Offene Freigaben überstehen das Kürzen langer Gespräche. Modell-, Gedächtnis- und Werkzeugaufrufe werden ohne Inhalte in `~/.jarvis/events.jsonl` protokolliert. Der Systemprompt beschreibt die tatsächlichen Runtime-Grenzen. Details, Teststand und Rückweg: `docs/BRAIN-UMBAU-2026-10-17.md`.

## V4: adaptive Oberfläche (Command Center auf Port 8080)

Im Gespräch sieht man nur den orangefarbenen Voice-Core. Inhalte öffnen sich erst, wenn sie zum Gespräch passen:

| Ansicht | Wann | Inhalt |
|---|---|---|
| Gespräch | Standard, Smalltalk, kurze Antworten | nur die Blase (Atmen, Zuhören, Verarbeiten, Sprechen folgen den echten Audiopegeln) |
| Nachrichten / Welt | „Was gibt es Neues?“, „Zeig mir die Nachrichten“, „Was ist in Paris los?“ | Cesium-Erde mit Licht und Atmosphäre; Markierungen nur bei eindeutig genanntem Ort; Quelle und Zeit je Meldung |
| Märkte | eindeutige Wertpapier-Anfrage („Wie hat sich Apple entwickelt?“, „Zeig mir den Tesla-Kurs“) | großes Kursdiagramm mit Name, Symbol, Börse, Währung, Quelle, Stand; mehrdeutige Namen → Rückfrage |
| Kameras / Gedächtnis / System | nur gezielt (Menü ☰ oder ausdrücklicher Befehl) | CCTV-Grid, Gedächtnis, Metriken/Transkripte/Protokoll |

- **Bedienung:** Mikrofon, Texteingabe, Stopp (■, Esc) und Menü (☰) erscheinen bei Maus-/Touch-Bewegung oder Tastaturfokus (Tab, „/“ springt ins Eingabefeld). „Zurück“, „Nur mit dir sprechen“, „Schließ die Ansicht“ oder × führen zurück.
- **Entscheidung:** nur aus der *fertigen* Nutzeräußerung (`dashboard/src/view/intent.ts`) und strukturierten Werkzeug-Ereignissen (`TOOL`), nie aus laufenden Modell-Tokens. Negationen („Ich mag die Nachrichtenansicht nicht“) öffnen nichts. Manuell geöffnete Ansichten bleiben gegen schwache Hinweise stehen. Ereignisse eines abgebrochenen oder älteren Sprachzugs werden verworfen (`view/controller.ts`).
- **Laufzeit:** WebSocket, AudioEngine, Mikrofon und Raum liegen außerhalb der Ansichten; ein Wechsel initialisiert nichts neu (Integrationstest `src/app.test.tsx`). Cesium wird erst mit der Weltansicht geladen und beim Schließen freigegeben.
- **Zweiter Monitor:** nur über ☰ → „Operations auf Screen 2“; öffnet nie automatisch. Der sichtbare Ansichtszustand wird per BroadcastChannel gespiegelt, Audio und Tokens nie.
- **Jobs & Freigaben:** weiterhin in der nativen Oberfläche http://localhost:8765 (Link im Menü und in der System-Ansicht).

### Kursdaten (optional)

Ohne Konfiguration zeigt die Chartansicht ehrlich „Datenquelle nicht eingerichtet“ – es werden keine Kurse erfunden.

```bash
export ALPHAVANTAGE_API_KEY='DEIN_SCHLUESSEL'   # offizielle Alpha-Vantage-API, Tagesschlusskurse (~100 Handelstage)
# oder ausdrücklich gekennzeichnete Demo-Daten:
export JARVIS_MARKET_PROVIDER=demo
```

Im Demomodus (`--demo`) werden automatisch gekennzeichnete Demo-Kurse verwendet. Schlüssel bleiben im Backend (Python-Brücke); keine Orderausführung.

## Auf dem Mac starten

Im Terminal einfach `jarvis` eingeben (Alias in `~/.zshrc` auf `~/Desktop/jarvis/jarvis-start.sh`); `jarvis-demo` startet den Demomodus. `jarvis-start.sh` setzt Java 21 (`~/.local/tools/jdk-21…`) und Python 3.12 (uv) und ruft `start-jarvis-v3.sh` auf. Das Frontend und das Java-JAR sind bereits gebaut; Node/Maven werden nur bei einem Neubau benötigt.

```bash
jarvis          # Echtmodus
jarvis-demo     # Demo ohne KI-Kosten
```

Der Start installiert fehlende Python-Abhängigkeiten isoliert, erzeugt einen zufälligen internen Bridge-Token und startet die Dienste gemeinsam. **Demo bedeutet synthetische Antworten und WAV-Testton, aber echtes dauerhaftes Gedächtnis.** Keine persönlichen Konten werden in der Demo aufgerufen.

- Dashboard (öffnet `jarvis` automatisch): **http://localhost:8080** – Übersicht, Aufträge, Kalender, Agenten, Automatisierungen, World View, Gespräch.
- Sprachoberfläche mit Jobs, Freigaben und Live-Kameras: **http://localhost:8765** (Link unten in der Dashboard-Navigation). Im Dashboard `OPEN OPS` für Bildschirm 2 anklicken und das Fenster auf den zweiten Monitor ziehen.
- Interne Agenten-Brücke: Loopback **8766**, ausschließlich Bearer-authentifiziert, kein direkter Browserzugriff.

`Strg+C` beendet alle gestarteten Prozesse. Native UI und Agenten-Brücke teilen einen Python-Prozess und damit Jobs/Freigaben; der Java-Server ist der zweite Prozess. Für denselben Audiofluss nicht gleichzeitig in beiden Oberflächen das Mikrofon einschalten. `--no-browser` unterdrückt das automatische Öffnen; `--no-native` startet nur Command Center und Brücke.

Der Echtmodus nutzt `ANTHROPIC_API_KEY` aus `~/.zshrc` bzw. `~/.jarvis.json`.

Im Echtmodus übernimmt die Brücke die vorhandene Stimmen-/Tempo-Konfiguration: ElevenLabs, sofern eingerichtet, sonst bestehender edge-tts-Fallback. Lokale Mikrofon-Transkription läuft über faster-whisper, ohne Audio-Hardware auf dem Server zu öffnen. Das erste Whisper-Modell wird bei Erstbenutzung heruntergeladen; `JARVIS_WHISPER_MODEL` wählt das Modell (Standard `base`). Vorhandene CLI-Befehle wie `python -m jarvis --mikro` und `--google-verbinden` bleiben verfügbar. Die native UI kann unabhängig mit `python -m jarvis --ui --demo` gestartet werden.

## Erweiterte Kern-Dateien

| Datei / Modul | Änderung |
|---|---|
| `jarvis/memory.py` | Dauerhaftes SQLite/WAL-Gedächtnis mit FTS5, lokalen gehashten Feature-Vektoren, relevanzgewichteter Suche, Gesprächsepisoden, Herkunft, Scope-Trennung und prozessübergreifender Cache-Invalidierung. |
| `jarvis/agent.py` | Persönliche Persona, relevante Recall-Auszüge, Wissenswerkzeuge, historische Kontextdaten, Episodenspeicherung und generationsgebundene Abbruchsignale; komplette alte Nutzerzüge werden vor neuen Modellanfragen begrenzt. |
| `jarvis/v3_bridge.py` | Authentifizierte lokale Verbindung zum vorhandenen Agenten, NDJSON-Streaming, requestgenauer Abbruch, Gedächtnis-CRUD, lokale STT und vorhandene TTS. |
| `java-backend/.../provider/` | Room-/Request-Kontext, reactive Python-Bridge-Provider, vollständige MP3-Ausgabe im Echtmodus; expliziter Offline-Demo-Testton. |
| `java-backend/.../room/` und `.../web/` | `STOP_STREAM`, Output-Purge, Cancellation, Owner-/Observer-Sperren, serverseitige PCM-VAD, begrenzte Aufnahmen/Queues und gemessene JVM-Metriken. |
| `dashboard/src/audio.ts` | AudioWorklet, kontinuierliche 20-ms-PCM-Frames, clientseitige VAD, Vorlauf, sofortiges lokales Stoppen und generationserfasste Stop-Signale. |
| `dashboard/src/components/` | Cesium-Globus, Cinematic Deep Zoom, Tactical HUD, HLS/MP4/Mock-CCTV, simulierte Mock-PTZ, getrennte Operationsansicht und Gedächtnisverwaltung. |
| `jarvis/webui.py`, `jarvis/ui/*` | Native Gedächtnisansicht, gemeinsame CRUD-Endpunkte, Command-Center-Link; alte Jobs/Tools/Freigaben bleiben nutzbar. |
| `jarvis-start.sh`, `start-jarvis-v3.sh`, `scripts/launch_v3.py` | Gemeinsamer Mac-/Linux-Start, Readiness, isolierte Umgebung und sauberes Prozess-Shutdown. |

## Gedächtnis, Kameras und Sicherheit

Standarddaten liegen unter **`~/.jarvis/memory.sqlite3`**; `JARVIS_DATA_DIR` kann einen anderen privaten Datenordner auswählen. Vorhandene sichere `/memories`-Dateien werden übernommen, nicht entfernt. Fakten und abgeschlossene/unterbrochene Gespräche bleiben über Neustarts erhalten; die Modellanfrage bekommt nur relevante Auszüge. Es wird **kein externer Embedding-Dienst** verwendet. Die gehashten Features sind ausdrücklich keine neuralen semantischen Embeddings. Schlüssel-/Passwort-/Code-/Zahlungsdaten werden mit Schutzfiltern abgewiesen bzw. aus Episoden bereinigt; das ist keine mathematische Garantie zur Erkennung jeder denkbaren vertraulichen Information. Die lokale Datenbank ist nicht verschlüsselt: den Datenordner entsprechend schützen. Das Löschen einer Notiz löscht diese Notiz, nicht automatisch sämtliche historischen Gesprächsepisoden.

Kameras sind standardmäßig klar markierte Simulationen. Berechtigte HTTPS-HLS-/MP4-Quellen über `JARVIS_CAMERAS_FILE=/absoluter/pfad/cameras.json` konfigurieren; Format siehe `cameras.example.json`. RTSP benötigt ein eigenes autorisiertes RTSP→HLS/WebRTC-Gateway. Live-PTZ bleibt ohne berechtigten Gerätesteuerungs-Endpunkt deaktiviert. Satellitenbilder verwenden den in der Cesium-Dokumentation beschriebenen öffentlichen ArcGIS-World-Imagery-Dienst mit sichtbarer Attribution; für Produktivbetrieb dessen Nutzungsbedingungen/Verfügbarkeit prüfen.

Screen 2 zeigt Transkripte, Memory-Ereignisse, JVM-Werte und **öffentliche Modell-/Tool-Ereignisse**, keine verborgenen Modell-Gedankengänge. `BroadcastChannel`/localStorage synchronisieren nur UI-Zustand; Audio und Tokens werden nicht gespiegelt. Bestehende Freigaben für externe Aktionen werden nicht umgangen. Das System startet lokal; öffentliches Hosting ist nicht eingerichtet.

## Tests / Neubau

```bash
# Python (die vom Launcher verwendete Umgebung oder eine eigene aktivieren):
python -m pip install pytest pytest-asyncio
python -m pytest -q
# Java:
cd java-backend && ./mvnw test package && cd ..
# Frontend, nur für Änderungen am Quellcode:
cd dashboard && pnpm install --frozen-lockfile && pnpm typecheck && pnpm test && pnpm build
```

Offline-Teststand (V4, 17.10.2026): Python **81 bestanden, 2 optionale Browser-Tests übersprungen**; Java **16 bestanden**; React/TypeScript **30 bestanden** (inkl. Ansichtsentscheidungen, Negation, manueller Vorrang, veraltete Ereignisse, stabile Audio-Laufzeit), Produktionsbuild enthalten. Zusätzlich wurde der Gesamtlauf mit drei WebSocket-Audiozügen, STOP_STREAM, Owner-/Observer-Trennung, anschließender PCM/STT-Verarbeitung, gemeinsamer nativer Job-Liste und Gedächtnis-Recall nach Prozessneustart geprüft. Lokale HTTP-Provider-Doubles ersetzen persönliche Konten. Physische Mac-Mikrofone/Lautsprecher und fremde Live-Kameras wurden nicht live getestet. Ein unmittelbarer lokaler Playback-/Buffer-Purge ist implementiert; tatsächliche Netz-, Modell-, STT- und Hardwarelatenz bleibt messbar und ist nicht garantiert null.
