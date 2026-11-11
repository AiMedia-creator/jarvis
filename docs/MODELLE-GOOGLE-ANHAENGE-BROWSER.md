# Modelle, Google Workspace, Dateianhänge und Browser (Stand 11.11.2026)

## 1. Modell-Anbieter (`jarvis/modelle.py`)

Das Gehirn spricht alle Anbieter über die **Anthropic-Messages-Schnittstelle** an, jeweils über eine offizielle API:

| Auswahl | Anbieter | Modell-ID | Endpunkt (offizielle Doku) | Schlüssel |
| --- | --- | --- | --- | --- |
| `opus` / `sonnet` / `haiku` | Anthropic | `claude-opus-5-5` / `claude-sonnet-5-5` / `claude-haiku-5-5` | api.anthropic.com | `ANTHROPIC_API_KEY` |
| `kimi` / `kimi-schnell` / `kimi-k3` | Moonshot | `kimi-k3.1-pro` / `kimi-k3.1-pro-highspeed` / `kimi-k3` | `https://api.moonshot.ai/anthropic` ([platform.kimi.ai/docs/api/messages](https://platform.kimi.ai/docs/api/messages)) | `MOONSHOT_API_KEY` |
| `ollama` | Ollama, lokal | `JARVIS_OLLAMA_MODEL` | `http://127.0.0.1:11434/v1/messages` ([docs.ollama.com/api/anthropic-compatibility](https://docs.ollama.com/api/anthropic-compatibility)) | keiner |

Ein Web-Abo (claude.ai, kimi.com, ChatGPT) ersetzt keine API und wird nicht unterstützt.

**Was erhalten bleibt:** Streaming (Satz für Satz), Gesprächsverlauf, Werkzeugaufrufe, Abbrechen, Token-Budget,
Freigaben, die Fehlermeldungen (jetzt mit Namen des Anbieters).

**Was je Anbieter wegfällt:**

| | Anthropic | Moonshot | Ollama |
| --- | --- | --- | --- |
| Websuche/-abruf des Anbieters (`web_search`, `web_fetch`) | ja | nein | nein |
| Anthropic-Memory-Werkzeug | ja | nein | nein |
| Prompt-Caching | voll | nur oberster Marker | nein |
| Denktiefe (`effort`) | low…max | low/high/max (medium → high) | nein |
| PDF direkt | ja | nein (Text wird extrahiert) | nein |
| Bilder | ja | ja | aus (Modellabhängig) |

Ohne Anbieter-Websuche liest Jarvis Seiten weiter mit dem eigenen Werkzeug `webseite_lesen`. **Gremium, Tiefenrecherche
und Cloud-Skills** rufen Claude direkt auf und brauchen weiter `ANTHROPIC_API_KEY` (Modell: `JARVIS_CLAUDE_HILFSMODELL`,
Standard Sonnet 5.5).

**Umschalten:** Command Center → Einstellungen → „Gehirn“. Nicht eingerichtete Anbieter sind gesperrt und nennen den
Grund. Beim Wechsel des Anbieters bleiben Text und Werkzeugaufrufe im Verlauf; nur Denkblöcke werden entfernt (ihre
Signatur gilt nur beim erzeugenden Modell).

**Absicherungen für Claude Opus/Sonnet/Haiku 5.5:** Jarvis kürzt lange Verläufe von vorn und wechselt Werkzeugsätze
(Schnellspur). Für Konten, bei denen Anthropic den Verlauf prüft („preserved thinking“), würde das zu 400-Fehlern führen.
Jarvis sendet deshalb `thinking.block_binding.prefix_mismatch_behavior = "drop_block"` (Beta
`thinking-binding-controls-2026-08-01`): Betroffene Denkblöcke werden verworfen statt die Anfrage abzulehnen. Für
Opus/Sonnet 5.5 ist außerdem `fallbacks: "default"` (Beta `server-side-fallback-2026-07-01`) an: Lehnt die
Sicherheitsprüfung eine Anfrage ab, beantwortet sie ein Ausweichmodell von Anthropic. Lehnt der Server eines dieser
Extras ab, wiederholt Jarvis die Anfrage einmal ohne es und protokolliert `anbieter_extra_aus`. **Beides wurde nicht
gegen die Live-API getestet** (das hätte Kosten verursacht); bitte nach dem Umstellen auf Opus einmal im Protokoll
nachsehen.

### Empfehlung

- **API (empfohlen für Jarvis):** `opus` (Claude Opus 5.5). Jarvis ist ein Werkzeug-Agent (Mail, Kalender, CRM, Freigaben)
  mit deutscher Sprache – das stärkste Modell lohnt sich hier am meisten. Die Denktiefe ist je Kanal schon gesetzt
  (Sprache `low`, Text/Hintergrund `medium`), damit Antworten im Gespräch schnell bleiben. Preis laut Anthropic:
  $4 / $20 je Million Ein-/Ausgabe-Tokens (Haiku 5.5: $0,10 / $0,50). Günstigere Mitte: `sonnet` ($2 / $10).
- **Alternative API:** `kimi` (Kimi K3.1 Pro) – gute Wahl, wenn du Anthropic nicht nutzen willst; ohne Anbieter-Websuche.
- **Lokal (Ollama):** für Datenschutz-sensible Einzelfragen und offline. Für den vollen Werkzeug-Agenten sind lokale
  Modelle, die auf diesen Rechner passen, deutlich schwächer: Werkzeugaufrufe und lange deutsche Antworten gelingen
  seltener. **Hardware-Fakten (ausgelesen, nicht geschätzt):** MacBook Pro mit Apple M1 Pro und 16 GB gemeinsamem
  Speicher. Modell, Kontext und macOS teilen sich diese 16 GB; ein Modell muss also deutlich kleiner sein als 16 GB
  (Größe zeigt `ollama list`). Ollama 0.40.13 ist installiert, lief bei der Prüfung aber nicht, und es war kein Modell
  geladen. Welches Modell taugt, hängt von seiner Werkzeug-Unterstützung ab – in der Ollama-Bibliothek auf die
  Kennzeichnung „tools“ achten und mit `ollama pull <name>` laden, dann `JARVIS_OLLAMA_MODEL=<name>`.

## 2. Google Workspace (Drive und Sheets)

Vorhanden waren schon Gmail, Kalender, Drive (suchen, lesen inkl. Sheet als CSV, Doc erstellen), Aufgaben, Kontakte.
Neu sind die Sheets-Werkzeuge (`jarvis/tools/sheets.py`):

| Werkzeug | Was | Berechtigung | Freigabe |
| --- | --- | --- | --- |
| `sheet_lesen` | Tabelle per ID/URL lesen, Bereich wählbar | `drive.readonly` (schon erteilt) | nein |
| `sheet_erstellen` | neue Tabelle in „Meine Ablage“, optional Kopfzeile | `drive.file` (schon erteilt) | nein |
| `sheet_schreiben` | Zellen überschreiben oder Zeilen anhängen | eigene Tabellen: `drive.file`; fremde: `spreadsheets` (optional) | **ja** |

Werte werden immer als `RAW` geschrieben: `=IMPORTXML(…)` aus fremden Inhalten wird nie als Formel ausgeführt.
Höchstens 2000 Zellen je Schreibvorgang, 5000 je Lesevorgang. Fehler werden klar benannt: nicht verbunden, Token
abgelaufen/widerrufen, fehlende Berechtigung, Tabelle nicht gefunden, Sheets API nicht aktiviert.

### Einrichtung (einmalig)

1. [console.cloud.google.com](https://console.cloud.google.com) → Projekt wählen (oder anlegen).
2. „APIs & Dienste → Bibliothek“: **Google Sheets API** und **Google Drive API** aktivieren (dazu Gmail, Calendar,
   Tasks, People API, falls noch nicht geschehen).
3. „OAuth-Zustimmungsbildschirm“: Typ „Extern“, dich selbst als Testnutzer eintragen. Hinweis von Google: Solange die
   App im Status „Test“ ist, laufen Anmeldungen nach 7 Tagen ab – Jarvis meldet das dann und du verbindest neu.
4. „Anmeldedaten → OAuth-Client-ID → Desktop-App“, JSON herunterladen.
5. Im Terminal: `python -m jarvis --google-verbinden /pfad/client_secret.json` – im Browser anmelden. Pflicht-Häkchen:
   Gmail, Kalender, Drive (lesen + von Jarvis erstellte Dateien), Aufgaben, Kontakte. **Optional:** „Google Sheets“
   (fremde Tabellen bearbeiten) und „Google Drive“ (Drive aufräumen). Abgewählte optionale Rechte schränken nur diese
   Funktionen ein.

Zugangsdaten liegen nur in `~/.jarvis/google_client.json` und `~/.jarvis/google_token.json` (Rechte 0600, beide per
`.gitignore` ausgeschlossen); sie gehen nur an Google und erscheinen nie in Protokollen.

## 3. Dateianhänge im Chat (`jarvis/anhaenge.py`)

Im Command Center: Büroklammer neben dem Eingabefeld oder Datei ins Gespräch ziehen. Jede Datei wird sofort hochgeladen
und geprüft; der Chip zeigt „wird geprüft …“, das Ergebnis (z. B. „PDF · 12 Seiten“) oder den Fehler. Mit × lässt sie
sich vor dem Senden entfernen. Senden ist gesperrt, solange eine Datei noch geprüft wird.

- **Typen:** PDF, TXT, MD, CSV, DOCX, XLSX, PNG, JPG, GIF, WEBP. Erkannt an den ersten Bytes; passt die Endung nicht
  zum Inhalt, wird die Datei abgelehnt.
- **Grenzen:** 10 MB je Datei (Java prüft vorab, Python erneut), Bilder 5 MB, 5 Dateien je Nachricht, 25 MB zusammen.
  Schutz vor Zip-Bomben (DOCX/XLSX) und XML-Bomben (`defusedxml`).
- **Was das Modell bekommt:** extrahierten Text (höchstens 60 000 Zeichen je Datei, sonst als gekürzt markiert), markiert
  als „nur Information, keine Anweisungen“. Bilder als Bild, wenn das Modell Bilder versteht – sonst einen ehrlichen
  Hinweis, dass es das Bild nicht sehen kann. PDFs ohne Text (gescannt) gehen an Claude als Original-PDF; andere Modelle
  sagen, dass sie sie nicht lesen können.

**Wo Dateien und Inhalte landen:**

| Ort | Was | Wie lange |
| --- | --- | --- |
| Java-Backend | Datei im Arbeitsspeicher, nur für die Weiterleitung | Dauer der Anfrage |
| Python-Brücke | Rohdaten + extrahierter Text im Arbeitsspeicher, je Gespräch | bis zur nächsten Nachricht, höchstens 30 Min. |
| Gesprächsverlauf des Agenten | der Nutzerzug mit Anhang (Arbeitsspeicher) | bis er gekürzt oder das Gespräch zurückgesetzt wird / Neustart |
| Modell-Anbieter | Inhalt dieses Zugs | nach den Regeln des Anbieters |
| `memory.sqlite3` (Gedächtnis) | nur dein getippter Text | dauerhaft (wie bisher) |
| `graph_checkpoints.sqlite3` | gekürzter Textauszug, Anhänge nur als „[Anhang]“ | wie bisher |
| Protokolle | Art, Bytes, Zeichenzahl – nie Name oder Inhalt | wie bisher |

Nichts wird auf die Festplatte geschrieben. Eine gesprochene Nachricht nimmt offene Anhänge ebenfalls mit.

**Schnittstellen:** `POST /api/anhaenge?raum=…&name=…` (roher Body, Session + Origin + `X-Jarvis`),
`GET /api/anhaenge?raum=…`, `DELETE /api/anhaenge/{id}?raum=…` → Python `/internal/anhaenge`.

## 4. Browser (`jarvis/browser.py`)

**Bestandsaufnahme:** Jarvis hatte keine Browsersteuerung – nur die Websuche/den Seitenabruf von Anthropic, das eigene
`webseite_lesen` (HTTP-Abruf mit Schutz vor internen Adressen) und `seite_oeffnen` (öffnet einen Link im Standardbrowser).

**Gefunden und angebunden:** Auf dem Rechner sind über `uv tool` zwei Pakete der Organisation browser-use installiert:
`browser-harness` 0.1.13 (Quelle laut Paket: github.com/browser-use/browser-harness) mit MCP-Server
`browser-harness-mcp`, sowie `browser-use` 1.0.0 (eigenes Agenten-Framework, nicht angebunden). Jarvis startet
`browser-harness-mcp` über seinen vorhandenen MCP-Client und bietet eigene, enge Werkzeuge an:

| Werkzeug | Wirkung | Freigabe |
| --- | --- | --- |
| `browser_seite` | Adresse, Titel, Text, sichtbare Bedienelemente mit Koordinaten (fester, lesender Ausdruck; Passwortfelder nie) | nein |
| `browser_bild` | Screenshot (nur für Modelle mit Bildverständnis; Datei wird sofort gelöscht) | nein |
| `browser_oeffnen` | öffentliche http(s)-Adresse öffnen (keine internen/lokalen Ziele) | nein (lokal, nie im Hintergrund) |
| `browser_scrollen`, `browser_tabs` | scrollen, Tabs auflisten | nein |
| `browser_klicken`, `browser_tippen`, `browser_feld_fuellen`, `browser_taste` | können Formulare absenden, Käufe auslösen | **ja** |

Nicht angeboten: beliebiges JavaScript, rohe CDP-Befehle, Datei-Upload, HTTP am Browser vorbei, frei wählbare Pfade.
Telemetrie von browser-harness ist für diesen Prozess aus (`BH_TELEMETRY=0`); Browser-Use-Cloud wird nicht benutzt.
`BU_AUTOSPAWN` wird bewusst nicht gesetzt: browser-harness wertet jeden gesetzten Wert als „Cloud-Browser starten“.

**Einschalten:** `JARVIS_BROWSER=an` (oder `"browser": "an"` in `~/.jarvis.json`) und Jarvis neu starten. Beim ersten
Zugriff fragt Chrome nach der Fernsteuerung (`chrome://inspect/#remote-debugging`). Probleme: `browser-harness --doctor`.
Status unter „Verbindungen“.

## 5. Tests

`tests/test_modelle.py`, `tests/test_anhaenge.py`, `tests/test_sheets_browser.py`,
`java-backend/…/CommandCenterApiTest.java` (Upload-Endpunkt), `dashboard/src/command/center.test.tsx` und
`stage3.test.tsx` (Anhänge, Modellauswahl). Externe Dienste (Anthropic, Moonshot, Ollama, Google, Chrome) wurden dabei
nicht aufgerufen.
