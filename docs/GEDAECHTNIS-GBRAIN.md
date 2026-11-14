# Gedächtnis: Sicherung, Export und gbrain (11.11.2026)

**Kurz:** `~/.jarvis/memory.sqlite3` bleibt das führende Gedächtnis und wird von allem hier nur gelesen. gbrain
(github.com/garrytan/gbrain, MIT) läuft **parallel und ausgeschaltet**; eingeschaltet liefert es ein zusätzliches,
lesendes Suchwerkzeug. Speichern, Bestätigen, Korrigieren und Löschen laufen unverändert über Memory v1
(`docs/MEMORY-V1.md`).

## Wo das Gedächtnis liegt

| Was | Ort | Format |
| --- | --- | --- |
| Erinnerungen | `~/.jarvis/memory.sqlite3`, Tabellen `facts`, `fact_versions` | SQLite (WAL), FTS5-Index `facts_fts` |
| Gesprächsarchiv | dieselbe Datei, `episodes` (+ `episodes_fts`) | SQLite |
| Ereignisprotokoll | dieselbe Datei, `memory_events` | SQLite |
| Alte Notizdateien | `~/.jarvis/memories/*.md` | Markdown (werden nicht mehr mitgeschickt) |
| Profil | `~/.jarvis/profil.md` | Markdown |
| Aufträge, Anrufe, Leads | dieselbe SQLite-Datei (`orders`, `anrufe`, `leads`, …) | SQLite |

Jede Zeile trägt `owner_scope` und `installation_scope`. Bestätigt ist eine Erinnerung, wenn `consent` nicht `vorschlag` ist.

## Befehle

```bash
python scripts/gedaechtnis.py sichern        # geprüfte Sicherung
python scripts/gedaechtnis.py pruefen ORDNER # Sicherung erneut prüfen (Prüfsummen, Integrität, Zeilenzahlen, Feldsummen)
python scripts/gedaechtnis.py probe ORDNER   # Wiederherstellung in einem temporären Ordner proben und vergleichen
python scripts/gedaechtnis.py exportieren    # Markdown-Export (ohne Gesprächsarchiv)
python scripts/gedaechtnis.py rueckweg       # Export -> neue Test-DB -> Feldvergleich
python scripts/gedaechtnis.py gbrain         # gbrain lokal einrichten und Export importieren
python scripts/gedaechtnis.py status
```

## Sicherung

- Ziel: `~/.jarvis/backups/gedaechtnis-<JJJJMMTT-HHMMSS>/` (Ordner 0700, Dateien 0600).
- Inhalt: `memory.sqlite3` (über die SQLite-Backup-API, konsistent auch bei laufendem Jarvis), `profil.md`,
  `memories/`, `MANIFEST.json` mit SHA-256 je Datei, erwarteten Zeilenzahlen aller Tabellen (`zeilen`) und einer
  SHA-256-Summe über alle Felder der Kerntabellen (`feldsummen`).
- Prüfung bei jeder Sicherung (seit 12.11.2026): Zählung der Quelle, Kopie und Gegenzählung laufen in **einem
  Lesesnapshot**; die Kopie muss den Zeilenzahlen **exakt** entsprechen (vorher galt „höchstens so viele“, eine
  gekürzte, aber gültige SQLite-Datei fiel nicht auf). Dazu `PRAGMA integrity_check`, Feldsummen, FTS-Index abfragbar,
  gleich groß wie `facts` und jede Erinnerung im Index. Danach wird die Wiederherstellung in einem temporären Ordner
  geprobt und mit dem Ausgangsbestand verglichen. Schlägt etwas fehl, heißt der Ordner `…-FEHLERHAFT` und der Befehl
  endet mit Fehler.
- `pruefen` vergleicht bei jedem Lauf erneut gegen die Zeilenzahlen und Feldsummen im Manifest. Ein Manifest ohne
  Zeilenzahlen gilt als nicht prüfbar. Sicherungen von vor dem 12.11.2026 haben keine Feldsummen: `pruefen` vergleicht
  dort die Kerntabellen, `probe` verlangt eine neue Sicherung.
- Die führende Datenbank wird dabei nur lesend geöffnet (`mode=ro`), nie verändert oder migriert.
- **Erste Sicherung:** `~/.jarvis/backups/gedaechtnis-20261111-221805/` (2 Erinnerungen, 358 Gesprächseinträge,
  405 Ereignisse, 4 Aufträge; Prüfung bestanden).

### Wiederherstellen (Rückfallweg)

1. Jarvis beenden (`./jarvis-start.sh` läuft nicht mehr).
2. `mv ~/.jarvis/memory.sqlite3 ~/.jarvis/memory.sqlite3.alt` und `rm -f ~/.jarvis/memory.sqlite3-wal ~/.jarvis/memory.sqlite3-shm`.
3. `cp ~/.jarvis/backups/gedaechtnis-<Zeit>/memory.sqlite3 ~/.jarvis/memory.sqlite3 && chmod 600 ~/.jarvis/memory.sqlite3`.
4. Jarvis starten. `gbrain_bruecke.wiederherstellen()` kopiert nur an einen neuen Ort und überschreibt nie.

## Export (Schema)

Je bestätigter Erinnerung des Scopes `local/default` eine Datei `~/.jarvis/gbrain-export/jarvis/erinnerungen/<id>.md`:

```
---
title: "…erste 80 Zeichen…"
type: "note"
tags: ["jarvis", "erinnerung", "<kategorie>"]
jarvis_id / jarvis_category / jarvis_source / jarvis_owner_scope / jarvis_installation_scope
jarvis_created_at / jarvis_updated_at / jarvis_valid_until / jarvis_confidence
jarvis_consent / jarvis_conflicts_with / jarvis_confirmed_at / jarvis_origin_episode / jarvis_client
jarvis_versionen: [ {content, category, source, changed_at, reason}, … ]
---
<Inhalt der Erinnerung>
```

Nicht exportiert: Vorschläge, andere Scopes, Inhalte mit Zugangsdaten. Das Gesprächsarchiv nur mit `--mit-archiv`
(eine Seite je Tag). IDs, Zeitstempel, Quelle, Bestätigung, Widersprüche und frühere Fassungen bleiben erhalten.
Beziehungen: `origin_episode` (Ursprungsgespräch) und `conflicts_with` bleiben als IDs erhalten; gbrain-Links daraus
entstehen nicht.

## Rückweg

`zurueckspielen()` baut aus dem Export eine neue SQLite-Datei mit demselben Schema und baut den Suchindex neu;
`vergleichen()` prüft je Erinnerung alle Felder, die Fassungen und einen FTS-Treffer. Getestet mit Testdaten
(`tests/test_gbrain_bruecke.py`) und mit dem echten Bestand: 2 von 2 gleich, keine Unterschiede.

## gbrain

- Installiert isoliert unter `~/.jarvis/tools/gbrain` (kein globales Paket), Commit
  `eb74799ee8d3debeea34cdd7cd2f30a3406e11af` = Tag `stable` = v0.60.654.0, Bun 1.4.4. Das Nachinstallationsskript
  (würde nur `gbrain apply-migrations` aufrufen) hat Bun blockiert.
- Eigenes Verzeichnis `~/.jarvis/gbrain` (`GBRAIN_HOME`), Engine PGLite, `embedding_disabled: true`. Ohne
  Embedding-Anbieter verlässt kein Inhalt den Rechner. gbrain prüft selbst auf neue Versionen (GitHub, Kanal „stable“).
- Jarvis reicht gbrain keine Schlüssel weiter (Umgebung ohne `*API_KEY*`, `*TOKEN*`, `*SECRET*`, `*PASSWORD*`).
- **Einschalten:** `JARVIS_GBRAIN=an` oder `"gbrain": "an"` in `~/.jarvis.json`, dann Jarvis neu starten. Dann gibt es
  das Werkzeug `gbrain_suchen` (nur lesen, als fremder Inhalt markiert). Es respektiert den Schalter
  „Erinnerungen an den Cloud-Anbieter senden“.
- **Cloud-Schutz:** Ist in gbrain ein Embedding-Anbieter eingerichtet und der Schalter aus, verweigert der Import.
- Stand: Die 2 bestätigten Erinnerungen sind importiert. Der Schalter ist **aus**.

## Grenzen

- gbrain ist ein Abbild. Änderungen in Jarvis erscheinen dort erst nach erneutem Export und Import; Löschungen in
  Jarvis entfernt der Import nicht aus gbrain (`gbrain delete <slug>` nötig).
- Umschalten auf gbrain als führendes Gedächtnis ist nicht gebaut: Vorschlag/Bestätigung, Versionsverlauf und
  Cloud-Schalter aus Memory v1 hätten dort keine Entsprechung.
- Ohne Embeddings ist die gbrain-Suche eine Stichwortsuche, also nicht besser als FTS5 in Jarvis.
