# Brain-Umbau – 17.10.2026

Grundlage: der laufende Jarvis (heute `~/Desktop/jarvis`) und `Claude-Jarvis-Brain-Systemprompt.md`.
Ein ZIP lag nicht bei; der vorhandene Code auf dem Rechner war die Basis. Der Brain-Prompt ist eine Verhaltensvorgabe.
Die Regeln, die er von der Runtime verlangt, sind hier im Code umgesetzt.

## Bestandsaufnahme (vor dem Umbau)

| Bereich | Befund |
|---|---|
| Sprache / Einstieg | Python 3.12. `scripts/launch_v3.py` startet die Brücke (`jarvis/v3_bridge.py`, aiohttp :8766, plus native Oberfläche :8765) und Java WebFlux (:8080, React-Frontend). |
| Claude-Zugang | **Anthropic API** über das Python-SDK mit `ANTHROPIC_API_KEY` und Workspace-Header. **Kein Bedrock.** **Claude Code** wird zur Laufzeit nicht genutzt. Der Echtzeit-Sprachmodus nutzt Claude über ElevenLabs. Zugang, Modellanbieter und Deployment sind unverändert. |
| Orchestrator | `Agent.ask` (`jarvis/agent.py`): eine Schleife mit höchstens 20 Runden, Token-Budget, Abbruch, Werkzeug-Timeouts, Job-Status (`jobs.py`). |
| Persistenz | SQLite/WAL `~/.jarvis/memory.sqlite3`: Gedächtnis, Jobs und Freigaben. Dazu Audit- und Telemetrie-JSONL. |
| Freigaben | Vorlage und Freigabe, gebunden an unveränderte Angaben und an einen späteren Nutzerzug; nach Absturz UNCLEAR. |
| Lücken | Werkzeuge mit Nebenwirkung, aber ohne Freigabe (Aufgabe, Entwurf, Dokument, Archivieren, Gedächtnis, Hintergrundjob) waren ungeschützt gegen Doppelausführung und Absturz. Gedächtnis ohne Gültigkeit und ohne Widerspruchserkennung. Kürzen des Verlaufs ohne Zustandserhalt. Audit-Werte ungeschwärzt. Kein Protokoll für Modell- und Gedächtnisaufrufe. Prompt ohne Beschreibung der Runtime-Grenzen. |

## Umgesetzt

| Datei | Änderung |
|---|---|
| `jarvis/policy.py` (neu) | **Eine Prüfstelle für alle Kanäle** (Text, Stimme, Echtzeit, Hintergrund). Teilt jedes Werkzeug in `read`, `external`, `write` oder `local` ein; unbekannte gelten als `write`. Für `write`/`local` führt sie ein Ausführungsprotokoll (`tool_ledger`) mit den Zuständen STARTED → DONE / FAILED / UNCLEAR. **Doppelte Zustellung** wird nicht erneut ausgeführt, die Antwort ist „Bereits ausgeführt“. Läuft ein Aufruf noch, ist er abgestürzt oder gab es einen Netzfehler mitten in der Aktion, wird **nie automatisch wiederholt**, sondern „unklar“ gemeldet. Höchstens 12 schreibende Aufrufe pro Auftrag. Hintergrundaufträge dürfen nichts lokal öffnen. Die Schema-Prüfung läuft vor jeder Wirkung. |
| `jarvis/persist.py` | Tabelle `tool_ledger`. Beim Start werden offene STARTED-Einträge zu UNCLEAR. |
| `jarvis/agent.py` | Alle Werkzeuge (auch `memory`, `wissen_*`) laufen durch `policy.execute`. **Zustandsvermerk:** Jeder Nutzerzug bekommt aus der Runtime offene Freigaben und unklare Aktionen vorangestellt, als Fakten gekennzeichnet. So überstehen sie jedes Kürzen. Beim Kürzen ein Hinweis und ein Ereignis. Neuer Systemprompt-Abschnitt (Wahrheit, nicht vertrauenswürdige Inhalte, technisch erzwungene Werkzeugregeln, Freigaben, Gedächtnis, Grenzen). Gedächtnis-Werkzeuge mit `gueltig_bis`, Widerspruchs-Status und Löschbericht. Die automatische „Merke dir“-Erkennung greift nur noch, wenn das Modell nichts gespeichert und nicht nachgefragt hat. Das Job-Ergebnis wird vor dem Abschluss eingetragen (behebt einen Wettlauf). |
| `jarvis/memory.py` | Neue Spalten `valid_until`, `confidence`, `consent`, `conflicts_with`, angelegt per `ALTER TABLE`. Identische Notizen werden nicht doppelt gespeichert. Ähnliche, abweichende Notizen derselben Kategorie werden **beide behalten und als Widerspruch markiert**. Abgelaufene Notizen werden ausgeblendet. Kategorien sind vereinheitlicht (Vorliebe → preference). `forget_report()`: was gelöscht wurde und was noch in Gesprächsverläufen oder Backups stehen kann. Die Trennung nach Scope bleibt. |
| `jarvis/events.py` (neu) | Redigiertes Ereignisprotokoll `~/.jarvis/events.jsonl` (Rechte 600, rotiert ab 5 MB) für Modellaufrufe (Tokens, Stop-Grund), Gedächtnissuchen (nur Anzahlen), Werkzeuge (Klasse, Status, ms), Kontextkürzung und Fehler (nur Fehlerart). Keine Inhalte. |
| `jarvis/audit.py` | Argument- und Ergebniswerte werden jetzt mit denselben Mustern wie das Gedächtnis geschwärzt. |
| `jarvis/eleven_agent.py` | Werkzeuge im Echtzeitmodus laufen durch dieselbe Prüfstelle. |
| `jarvis/webui.py` | Lese-Endpunkte `GET /api/aktionen` (Ausführungsprotokoll) und `GET /api/ereignisse` (Ereignisse), nur lokal mit Host-Prüfung. |

Es gibt keine neuen Abhängigkeiten, nur die Standardbibliothek und vorhandene Pakete. Es wurde kein fremder Code übernommen, daher keine Lizenzfragen.

## Tatsächlich getestet

- `python -m pytest -q`: **111 bestanden, 2 übersprungen**, zweimal hintereinander. Die Isolationstests liefen fünfmal einzeln.
- Neu: `tests/test_policy.py` (9), `tests/test_memory_brain.py` (10), `tests/test_context_events.py` (5). Sie decken ab:
  - Klassifikation und Schema-Prüfung vor der Wirkung
  - doppelte Zustellung und 6 parallele Zustellungen (genau eine Ausführung)
  - Absturz mitten in der Aktion: UNCLEAR, keine Wiederholung
  - Netzfehler: unklar; Fehler vor der Wirkung: neuer Versuch erlaubt
  - Grenze pro Auftrag, Hintergrund ohne lokale Rechte
  - Agent-Ende-zu-Ende mit wiederholtem Aufruf
  - Gedächtnis: Duplikat, Widerspruch, Ablauf, Löschbericht, Scope-Isolation (auch für Widersprüche), Zugangsdaten abgelehnt
  - Gedächtnis-Text im Prompt als nicht vertrauenswürdig markiert, Rückfallebene ohne Doppel- oder Vorab-Speicherung
  - Zustandsvermerk mit offener Freigabe nach Kürzen
  - Ereignis- und Audit-Log ohne Inhalte und Zugangsdaten
- Unverändert grün: die vorhandenen Tests für Freigaben (inkl. Prompt Injection im selben Zug), Persistenz und Neustart, Isolation sowie Java 17/17 und Dashboard 30/30.
- **Live** mit echtem API-Schlüssel in einer getrennten Instanz (eigener Datenordner):
  - Zwei widersprüchliche „Merke dir“-Aufträge: Jarvis fragt nach und speichert nichts vorab.
  - Eine Nachrichtenfrage mit Werkzeug: Antwort nach 9,7 s, Ereignisse protokolliert.

## Noch offen

- Echte schreibende Google- und HubSpot-Aktionen habe ich nicht live ausgelöst, um keine echten Nebenwirkungen zu erzeugen. Sie sind durch Tests mit Nachbildungen abgedeckt.
- Unklare Aktionen werden nicht automatisch mit dem Zielsystem abgeglichen, z. B. „Gibt es die Aufgabe in Google Tasks?“. Jarvis meldet sie und fragt.
- Die Widerspruchserkennung ist wortbasiert (lokale Hash-Vektoren, kein Embedding-Dienst). Umformulierte Widersprüche bemerkt das Modell über den Gedächtnis-Auszug. Im Live-Test hat das geklappt, garantiert ist es nicht.
- Das Löschen einzelner Gesprächsepisoden ist weiterhin nicht als Werkzeug verfügbar.
- Die Oberfläche zeigt `/api/aktionen` und `/api/ereignisse` noch nicht an; sie sind nur per Browser/JSON abrufbar.

## Konfiguration (optional)

| Variable | Wirkung |
|---|---|
| `JARVIS_FREIGABE_NUR_KLICK=true` | Freigaben nur per Klick |
| `JARVIS_JOB_TOKEN_BUDGET` | Token-Grenze pro Auftrag (Standard 600 000) |
| `JARVIS_EFFORT` | Denktiefe (low/medium/high), Standard je Kanal |

Grenzwerte im Code: `policy.MAX_WRITES_PER_TURN = 12`, `policy.DEDUP_WINDOW` (10 min für write, 20 s für local), `policy.UNCLEAR_BLOCK = 24 h`.

## Migration und Rückweg

- **Migration:** automatisch beim Start. `tool_ledger` wird mit `CREATE TABLE IF NOT EXISTS` angelegt, die Gedächtnis-Spalten per `ALTER TABLE ADD COLUMN` mit Standardwerten. Bestehende Daten bleiben unverändert und gültig.
- **Starten:** im Terminal `jarvis` (oder `~/Desktop/jarvis/jarvis-start.sh`), Demo: `jarvis-demo`.
- **Rückweg:** Die Sicherung `backup-vor-brain-20261017-2144.zip` wurde auf Wunsch in den Papierkorb verschoben (dort wiederherstellbar, bis er geleert wird). Alle alten Versionen und Archive liegen seit dem Umzug nach `~/Desktop/jarvis` im Papierkorb. Die zusätzlichen Spalten und die Tabelle stören eine ältere Version nicht; sie werden ignoriert.
