# Jarvis Memory v1 (11.11.2026)

## Speicherorte (getrennt)

| Art | Ort | Wer schreibt |
| --- | --- | --- |
| **Erinnerungen** | `~/.jarvis/memory.sqlite3`, Tabelle `facts` (+ `fact_versions` für frühere Fassungen) | nur du: „Merk dir …“ im Chat, Gedächtnis-Center, oder dein Ja zu einem Vorschlag |
| **Chatverlauf** | dieselbe Datei, Tabelle `episodes` (Gesprächsarchiv) | jede Nachricht, wie bisher |
| **Dateianhänge** | nur Arbeitsspeicher bis zur nächsten Nachricht (`jarvis/anhaenge.py`) | Upload im Command Center |

Die Datei liegt nur auf diesem Mac (Rechte 0600 über `~/.jarvis`) und übersteht Neustarts. Keine Schlüssel in der
Datenbank: Inhalte mit Zugangsdaten, Codes oder Bankdaten werden abgelehnt (`memory._contains_secret`).

## Regeln (im Code: `jarvis/erinnerungen.py`, `jarvis/agent.py`)

- **Speichern nur ausdrücklich:** „Merk dir, dass …“, „Merke dir …“, „Notier dir …“, „Speicher dir …“. Jarvis
  beantwortet das lokal, ohne Modellaufruf.
- **Unklares wird bestätigt:** Frage, Vermutung („vielleicht“, „glaube“ …), sehr knapper Inhalt oder Widerspruch zu einer
  älteren Erinnerung → Vorschlag + „Soll ich mir … merken? Ja/Nein“. Ohne Inhalt („Merk dir das“) fragt Jarvis nach.
- **Keine automatische Ableitung:** Die frühere Erkennung von „Ich bevorzuge …“ / „Mein Projekt … ist …“ ist entfernt.
  Das Modell kann über `wissen_speichern` nur einen **Vorschlag** anlegen; er wird nie verwendet, bis du zustimmst.
  Das Anthropic-Memory-Werkzeug (Dateien in `~/.jarvis/memories`) wird nicht mehr angeboten, alte Dateien werden nicht
  mehr mitgeschickt. Der Echtzeit-Sprachmodus legt mit `merken` ebenfalls nur Vorschläge an.
- **Gezielter Abruf:** Pro Frage höchstens 5 bestätigte Erinnerungen mit echtem Volltexttreffer (`erinnerungen.relevant`).
  Kein Treffer = keine Erinnerung. Früher gingen bis zu 25 „Dauer-Fakten“ immer mit – das ist entfernt.
- **Sichtbar:** Unter der Antwort steht „N gespeicherte Erinnerung(en) verwendet“ mit dem Inhalt.
- **Cloud-Schalter:** Einstellungen → „Passende Erinnerungen an den Cloud-Anbieter senden“ (`erinnerungen_an_cloud`,
  Standard an). Aus: An Anthropic/Moonshot gehen keine Erinnerungen, `wissen_suchen` liefert keine, und lokal erledigte
  Befehle landen nur als Platzhalter im Verlauf. Mit Ollama (lokal) bleiben Erinnerungen auf dem Mac.

## Verwalten

- **Chat:** „Zeig meine Erinnerungen“, „Lösche Erinnerung 2“, „Vergiss, dass …“, „Lösche alle Erinnerungen“ – Löschen
  immer erst nach „Ja“.
- **Gedächtnis-Center** (Navigation → System → Gedächtnis): ansehen, suchen, korrigieren (mit Versionsverlauf),
  Vorschläge bestätigen, einzeln löschen, „Alle Erinnerungen löschen“ (zweiter Klick), exportieren.

Löschen entfernt Erinnerung, frühere Fassungen und Suchindex-Eintrag. Der Chatverlauf bleibt (eigene Speicherart,
im Gedächtnis-Center unter „Gesprächsarchiv“ einzeln löschbar).

## Bekannte Grenzen

- Der Abruf ist eine Wortsuche (SQLite FTS5) ohne Embeddings: Eine Erinnerung wird nur gefunden, wenn ein Wort der Frage
  darin vorkommt.
- `~/.jarvis/profil.md` (Profil) ist eine eigene, ältere Datei und geht weiterhin mit jedem Systemprompt mit.
- Die letzten Züge des Gesprächs (Chatverlauf) gehen wie bisher als Kontext mit; der Cloud-Schalter betrifft nur
  Erinnerungen.
