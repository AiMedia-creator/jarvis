# Cold-Call-CRM (11.11.2026)

Ersetzt den Abschnitt „Vertrieb“. Code: `jarvis/leads.py`, Routen `/api/leads*` (Java reicht an die Python-Brücke durch),
Oberfläche `dashboard/src/command/CrmPanel.tsx`. Daten liegen lokal in `~/.jarvis/memory.sqlite3`.

## Datenmodell

- `leads(id, request_id, version, created, updated, status, telefon_key, namens_key, data)`; `data` (JSON): Firma, Name,
  Rolle, Telefon, E-Mail, Website, Ort, Quelle, Telefon-Art, Projekt, Notiz, nächster Schritt, Follow-up-Datum,
  Termin (Beginn/Ende), letzte Aktivität, Zahl der Anrufe.
- `lead_aktivitaeten(id, request_id, lead_id, created, art, data)`; `art` = `anruf` oder `notiz`.
- Status (erweiterbar in `leads.STATUS`): Neu, Nicht erreicht, Mailbox, Follow-up, Meeting gebucht, Kein Interesse.

## Regeln

- **Ergebnis erfassen** schreibt in einer Transaktion: Aktivität, neuen Lead-Stand und genau eine Zeile in `anrufe`
  (Kennzahlen unter „Auswertung“ stimmen weiter). Es entsteht kein Kanban-Auftrag und kein Eintrag in einem fremden CRM.
- **Follow-up** verlangt ein Datum, **Meeting gebucht** Datum und Uhrzeit (Ende sonst +30 Minuten). Auf Wunsch entsteht
  eine Kalender-Vorlage, die erst nach Freigabe eingetragen wird. Ohne verbundenen Google-Kalender bleibt der Termin am
  Lead und die Oberfläche sagt das.
- **Duplikate:** gleiche normalisierte Telefonnummer wird immer abgelehnt (eindeutiger Index). Gleiche Firma + Name
  braucht eine ausdrückliche Bestätigung.
- **Keine stillen Änderungen:** jede Änderung sendet die Version; bei Konflikt (409) lädt die Oberfläche den aktuellen
  Stand. Jeder Klick trägt eine `request_id`; Doppelklick oder Netz-Wiederholung erzeugt nichts doppelt.
- **Anrufliste:** fällige Follow-ups, dann neue Leads, dann am längsten nicht Kontaktierte. Die Pfeile (und Pfeiltasten)
  blättern in genau der Reihenfolge, die gerade gefiltert ist; sie bleibt stabil, bis Filter oder Suche wechseln.
- **Call-Session** („Aufgaben starten“): Zähler aus gespeicherten Ergebnissen seit Session-Start, Richtwert 20 pro
  Stunde, Tempo ab der ersten Minute, nächster Lead, Zifferntasten 1–5 für das Ergebnis, `N` für die Notiz.
  Jarvis wählt nie selbst; die Nummer ist ein `tel:`-Link.
- **CSV-Import:** UTF-8, Kopfzeile, Spalten `firma,name,rolle,telefon,email,website,notiz`, höchstens **1 MB**
  (1 000 000 Bytes) und 2000 Datenzeilen – dieselbe Grenze in Oberfläche, Java (`MAX_CSV`) und `leads.CSV_MAX_BYTES`.
  Die Datei geht als Rohdaten an `/api/leads/import?ausfuehren=…` (nicht als JSON: der JSON-Weg war in Java und in der
  Brücke auf 16 KB begrenzt). Der Server liest streng UTF-8 (kein stilles Ersetzen), prüft erst alle Zeilen, schreibt bei
  einem Zeilenfehler nichts und nennt Fehler und Duplikate mit Zeilennummer; Duplikate werden übersprungen.
- **Seiten:** Die Übersicht liefert 50 Leads je Seite (`offset`, `limit` bis 200). Zahlen, Statusfilter, Suche und fällige
  Follow-ups rechnen über den vollständigen Bestand (keine Obergrenze mehr). Ein Schnappschuss (`snapshot`) friert die
  Reihenfolge je Filter und Suche ein (2 Stunden, im Arbeitsspeicher der Brücke); Seiten, Pfeile im Lead-Detail und die
  Call-Session lesen daraus und laufen über Seitengrenzen. `um=<Lead-ID>` liefert die Seite, auf der ein Lead liegt.
  Nach einem Neustart der Brücke entsteht ein neuer Schnappschuss.
- **Suche:** Text sucht in Firma, Name, Rolle, Ort und E-Mail; Telefonnummern nur, wenn die Eingabe wie eine Nummer
  aussieht.
- **Schreibrecht:** nur der Besitzer des Raums, serverseitig über ein Besitzer-Ticket geprüft (`docs/security-model.md`,
  Punkt 10). `canEdit` in der Oberfläche blendet nur Bedienelemente aus.
