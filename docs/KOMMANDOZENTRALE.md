# Kommandozentrale (Etappe 3 des Entwicklungsplans)

Stand: 18.10.2026. Neue Arbeitsansicht im React-Dashboard (`http://localhost:8080`, Menü ☰ -> „Kommandozentrale“
oder per Sprache „Zeig mir meinen Kalender“, „Öffne die Aufträge“, „Öffne die Freigaben“).

## Bereiche

- **Übersicht:** Kennzahlen (Termine heute, Konflikte, offene Freigaben, Aufträge in Arbeit, überfällig),
  nächster Termin, Heute/Morgen, wichtigste Aufträge.
- **Agenda:** eigene lineare Liste (Heute, Morgen, Datum) in der Kalender-Zeitzone; Markierung Vorbereitung
  (Termin in den nächsten 24 h), läuft gerade, Follow-up (bis 48 h danach). „Vorbereiten lassen“ /
  „Follow-up entwerfen“ fragen Jarvis erst auf Klick.
- **Kalender:** FullCalendar 6.1.22 (Standard, MIT) Monat/Woche/Tag, deutsch, KW, Jetzt-Linie, Konflikte farbig.
  Lädt beim Blättern den sichtbaren Zeitraum nach; wird nur beim Öffnen geladen (eigenes Bundle).
- **Aufträge (Kanban):** dnd-kit 6.3.1 (Maus, Touch, Tastatur) plus ‹ › je Karte. Spalten: Eingang, In Klärung,
  Geplant, In Arbeit, Warten auf Freigabe, Erledigt. Neue Aufträge über das Formular oder durch Jarvis
  (`auftrag_anlegen`, `auftrag_aktualisieren`, `auftraege_liste`).
- **Freigaben:** unveränderte Vorschau aus dem Dispatcher (als Text, nie als HTML), Ziel, Risiko, Ablauf-Countdown,
  Job- und Graph-Status; „Freigeben …“ verlangt eine zweite Bestätigung, „Verwerfen“ sofort.
- Links Navigation (inkl. Nachrichten, Märkte, Kameras, Gedächtnis, System), oben Suche und Status, rechts
  einklappbare Details/Freigaben (auf schmalen Bildschirmen anfangs eingeklappt).

## Datenmodell

- **Auftrag (BusinessOrder)** `jarvis/orders.py`, Tabellen `orders` + `order_events` in `memory.sqlite3`:
  ID, Titel, Kunde, Projekt, Priorität, Verantwortlicher, Frist, Status, Position, Checkliste, Abhängigkeiten,
  Quelle, Notiz, verknüpfte Jobs und Graph-Läufe, Version. Jede Änderung ist versioniert (falsche Version ->
  409 mit aktuellem Stand, die Oberfläche rollt zurück), idempotent (`request_id`), auditiert (vorher/nachher,
  Quelle) und läuft in einer Transaktion.
- Auftrag ≠ technischer Job ≠ Graph-Lauf: nur verknüpft (`orders.link`). Verschieben startet nie einen Auftrag
  an Jarvis (getestet: 0 Jobs nach Ziehen).
- **Kalender** `jarvis/calendar_data.py`: strukturiertes JSON je Termin (Event-ID, Kalender-ID/-Name, Titel,
  Beginn/Ende mit Offset oder Ganztagsdatum (Ende exklusiv), IANA-Zeitzone, Teilnehmer, Ort, Meetinglink,
  Serien-/Ausnahmebezug, Status, ETag). Alle im Google-Kalender ausgewählten Kalender, Paginierung, Serien als
  Einzeltermine, Ende-vor-Beginn wird als ungültig markiert statt geraten, Konflikte nur zwischen belegenden,
  nicht abgelehnten Terminen. Das Werkzeug `kalender_termine` nutzt jetzt dieselben Daten (mit IDs und Konflikten).

## Schnittstellen

- Python-Brücke (Bearer-Token): `GET/POST /internal/orders`, `PATCH /internal/orders/{id}`,
  `GET /internal/calendar`, `GET /internal/approvals`, `POST /internal/approvals/{id}/{freigeben|verwerfen}`,
  `GET /internal/jobs`.
- Java (Dashboard): `/api/orders`, `/api/calendar`, `/api/approvals`, `/api/jobs`. Schreiben nur mit Session,
  erlaubtem Origin und Header `X-Jarvis: 1`; Statuscodes (409/400/404) werden durchgereicht.

## Tests (18.10.2026)

- Python 178 bestanden (u. a. `tests/test_command_center.py`: Versionskonflikt, Idempotenz, Audit, Eingabeprüfung,
  Verknüpfung, Ganztag/Zeitumstellung/Serie/ungültig, Konflikte, Paginierung über mehrere Kalender, Brücke inkl.
  doppelter Freigabe).
- Java 23/23 (`CommandCenterApiTest`: Schreibschutz, 409-Durchreichung, Eingabeprüfung).
- Dashboard 42/42, Typprüfung, Build (`src/command/model.test.ts`, `src/command/command.test.tsx`: optimistisches
  Verschieben mit Rückweg, Konflikt, kein Jarvis-Auftrag beim Verschieben, Freigabe nur nach Bestätigung,
  Beobachter-Modus, offline, reduzierte Bewegung, Zeitzonen/Sommerzeit, Sprachbefehle).
- Live (getrennte Testinstanz, echter Kalender): Auftrag angelegt und per Maus verschoben (Version 2, Audit,
  0 Jobs), Kalender Woche mit echten Terminen, Termin-Vorlage von Jarvis in der Freigabezentrale verworfen ->
  Graph abgeglichen („cancelled“), kein Termin im echten Kalender.

## Bewusst noch nicht umgesetzt

- Kalender **ändern/löschen/synchronisieren** (nur Lesen; Anlegen weiter über `termin_erstellen` mit Freigabe).
- Bearbeiten von Checkliste, Abhängigkeiten und Verantwortlichem in der Oberfläche (Felder existieren, API kann es,
  die Karte zeigt sie an).
- shadcn/ui bzw. Radix wurden nicht eingeführt: Die vorhandenen eigenen Komponenten reichen; keine Bibliothek nur
  wegen eines Trends.
