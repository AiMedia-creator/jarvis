# Dashboard und Command Center – Neugestaltung (11.11.2026)

Übergabe zur Neugestaltung von „Heute“ (Dashboard) und Command Center im React-Dashboard (`dashboard/`).
Umgesetzt und geprüft; dieses Dokument hält Konzept, Regeln und Abnahmekriterien fest.

## 1. Produktdiagnose

Jarvis ist ein AgentOS: ein Hauptagent mit Rollen, Skills, Fähigkeiten, Abläufen und Freigaben. Die alte
Oberfläche bildete diese Bausteine einzeln ab (13 Navigationspunkte + 5 unter „Mehr“). Die Übersicht zeigte
sechs gleichrangige Karten mit Zählern, zwei konkurrierende Hauptflächen („Heute am wichtigsten“ und „Nächste
Umsatzaktion“) und einen allgemeinen Ratschlag, wenn keine Daten vorlagen. Das Command Center war ein Messpult
für die Sprachkette (Latenzen, Signalband, Turn-Tape); das Gespräch selbst zeigte nur einen Zug, die
Skill-Plattform war dort doppelt vorhanden.

Folge: Die Oberfläche beantwortete „Was kann Jarvis?“ statt „Was braucht mich jetzt, und wie gebe ich das
Nächste ab?“.

## 2. Zielkunde und Kernproblem

- **Zielkunde:** Solo-Unternehmer und sehr kleine Teams, die Vertrieb, Content, Aufträge und Termine selbst
  führen und Arbeit an Agenten abgeben.
- **Kernproblem:** Delegieren ohne Kontrollverlust. Wer Agenten arbeiten lässt, muss in Sekunden sehen, was
  auf ihn wartet, was läuft und was fertig ist – und das Nächste ohne Suche übergeben können.
- **Produktkern:** *Übergeben → Verfolgen → Entscheiden → Ergebnis.*

## 3. Zentraler Ablauf

1. **Heute** öffnet sich. Überschrift nennt die Lage („3 Dinge brauchen dich.“ / „Nichts wartet auf dich.“).
2. **Übergeben:** Befehlszeile „Was soll Jarvis erledigen?“ – Enter sendet und wechselt ins Command Center.
   Vorschläge füllen nur das Feld (nichts wird ungefragt gesendet). `⌘K`/`Strg+K` springt von überall in die Eingabe.
3. **Verfolgen:** Command Center zeigt Antwort und „Laufende Vorgänge“ (echte Jobs, Schritt x/y, Abbrechen);
   „Heute“ zeigt dieselben Jobs unter „Läuft gerade“.
4. **Entscheiden:** Freigaben stehen in „Braucht dich“ ganz oben und im Command Center unter „Wartet auf dich“;
   ein Klick führt zur unveränderten Freigabe-Ansicht mit zweiter Bestätigung.
5. **Ergebnis:** „Zuletzt fertig“ mit Ergebnisauszug; Klick öffnet den Job im Agenten-Bereich.

### Sofort sichtbar / erst bei Bedarf

| Sofort | Bei Bedarf |
| --- | --- |
| Lage-Satz, Befehlszeile, „Braucht dich“ (max. 6, Rest aufklappbar), laufende Jobs, letzte Ergebnisse, Termine heute/morgen | Kennzahlen zu Vertrieb/Content (in ihren Bereichen), Diagnose der Sprachkette (Taste `D`), World View/Nachrichten/Märkte/Kameras/Systemprotokoll (unter „Mehr“), Verbindungsstatus ohne Problem |

Status erscheint nur bei Abweichung: eine leere Kopfzeile bedeutet „alles in Ordnung“.

## 4. Informationsarchitektur

- **Jarvis:** Heute · Command Center · Freigaben · Agenten (auf dem Handy als untere Leiste + Menü, nie mehr als fünf)
- **Arbeit:** Aufträge · Kalender · Vertrieb · Content
- **System:** Skills und Automationen · Gedächtnis · Verbindungen
- **Mehr (eingeklappt):** World View · Nachrichten · Märkte · Kameras · Systemprotokoll

Abschnitts-IDs und Sprachbefehle sind unverändert (`sectionFromQuery`; „heute“ führt zusätzlich zur Startseite).

### „Braucht dich“ – feste Reihenfolge (`dashboard/src/command/today.ts`)

1. Freigaben (ein Agent ist blockiert, mit Restzeit)
2. Fehlgeschlagene/unterbrochene Jobs der letzten 24 h (höchstens 3)
3. Überfällige Aufträge (nach Priorität), dann heute fällige
4. Fällige Follow-ups aus erfassten Anrufen
5. Content im Status „review“
6. Terminkonflikte, nicht lesbarer Kalender
7. Anrufe ohne CRM-Eintrag, gestörte Abläufe, Verbindungen mit Handlungsbedarf

Keine Punktzahlen, keine Schätzungen: jeder Eintrag entspricht genau einem Datensatz. Quellen, die nicht
antworten, werden unter der Liste genannt („Nicht geprüft …“).

## 5. Entfallen, geändert, neu angeordnet

| Element | Entscheidung | Grund |
| --- | --- | --- |
| Karten „Heute am wichtigsten“ + „Nächste Umsatzaktion“ | ersetzt durch eine Liste „Braucht dich“ | zwei Hauptflächen konkurrierten; fällige Follow-ups bleiben als Einträge erhalten |
| Ratschläge ohne Daten („Neue Gespräche anstoßen“) | entfernt | nicht aus Daten ableitbar |
| Zähler-Kacheln Vertrieb/Content, Liste „Offene Aufträge“, Karte „Dienste“ | von der Startseite entfernt | keine Entscheidung; Abweichungen stehen in „Braucht dich“, Details in den Bereichen |
| Knopf „Briefing von Jarvis“ | Vorschlag „Tagesbriefing“ in der Befehlszeile | ein Weg statt zwei |
| Status-Pills „Verbunden“, „Kalender verbunden“ | nur noch bei Problemen | Normalzustand braucht keine Anzeige |
| Suche in der Kopfzeile der Startseite | entfernt (bleibt in Aufträge, Kalender, Freigaben) | Hauptaktion der Startseite ist die Übergabe |
| Command Center: Faktenzeile, 7-Segment-Zustand, Regie-Kette, Signalband, Turn-Tape, Unterbrechungswerte | vollständig erhalten, hinter „Diagnose“ (`D`) | Messwerte dienen der Fehlersuche, nicht dem Alltag |
| Command Center: Zustand | ein Zustands-Chip mit Pegel/Laufzeit/Werkzeug | derselbe Inhalt, ein Blickpunkt |
| Command Center: Transkript eines Zugs | ganzes Gespräch als Verlauf; Echtzeit-Züge getrennt markiert | der Verlauf ist die Hauptsache |
| Command Center: Skill-Plattform | dort entfernt (bleibt unter „Skills und Automationen“) | Duplikat |
| Command Center: „Einstellungen“ | von der Eingabeleiste in die Kopfleiste | Konfiguration ≠ Nachricht |
| Navigation in Marineblau, 4 Gruppen + Mehr | helle Leiste, 3 Gruppen, „Mehr“ eingeklappt, mobile Leiste | Kernablauf zuerst |
| `AgentsAtWork` (Karte) | entfernt | durch „Läuft gerade“ ersetzt |

Nicht angefasst: Kanban, Kalender, Vertrieb, Content, Agenten-Center, Gedächtnis, Verbindungen, Freigaben
(Logik und Aufbau unverändert; sie erben nur die neuen Farb- und Schrift-Tokens). Sprachbühne, Dock und die
Vollbild-Ansichten (Nachrichten, Märkte, Kameras, System) sind unverändert.

## 6. Designregeln („Werkbank“)

Quelle: `dashboard/src/command/command.css` (Tokens im Block `.cc`), `center.css`.

- **Nutzungsszene:** tagsüber am Schreibtisch, lange Sitzungen → helles Schema, fest.
- **Farbe mit fester Bedeutung:**
  - Tinte `#17191E` – Text und die eine Hauptaktion je Fläche.
  - Orange `#C2410C` / `#EA580C` – **braucht dich** (und Jarvis' Marke: der orange Punkt). Sonst nirgends.
  - Petrol `#0F766E` – **Jarvis arbeitet**, Auswahl, Links, Fokusring.
  - Rot nur für Fehler/Überfälliges, Ocker für Pflege-Hinweise, Grün für erledigt.
  - Flächen: Papier `#F5F4F0`, Karte `#FFFFFF`, Linien `#E2DFD8`.
- **Schrift:** IBM Plex Sans (400/500/600) für Oberfläche, JetBrains Mono für Zeiten, Zähler, Tasten. Grundgröße
  14,5 px, Eingaben 16 px, nichts unter 11,5 px.
- **Raster:** 4 px; Flächen 12 px, Bedienelemente 8 px Radius; Seitenrand 28 px (mobil 16 px).
- **Hierarchie:** höchstens eine gefüllte (Tinte-)Schaltfläche pro Fläche; Karten nur für Listen, keine
  verschachtelten Karten, keine Verläufe.
- **Bewegung:** 160 ms für Ein-/Übergänge, 80 ms Druck-Rückmeldung, Puls nur am Punkt eines laufenden Jobs;
  `prefers-reduced-motion` schaltet alles ab.
- **Zugänglichkeit:** Text ≥ 4,5:1 (nachgerechnet, kleinster Wert 4,63:1), Feldränder 3,3:1, sichtbarer
  Fokusring, Tastenkürzel `⌘K`, `/`, `M`, `S`, `D`, `Esc`; Tastflächen mobil ≥ 40–44 px; Bedeutung nie nur über Farbe
  (jede Zeile trägt ein Textlabel).
- **Breakpoints:** ≤ 1100 px einspaltig, ≤ 900 px Menü als Schublade + untere Leiste.

Herleitung: UI-UX-Pro-Max lieferte Stil „AI-Native UI“ (minimaler Rahmen, Gespräch im Zentrum), die Palette
„Productivity Tool“ (Petrol + Aktions-Orange) und das Paar IBM Plex Sans + JetBrains Mono. Bewusst abgewichen:
die violett-pinke Standardpalette und das Landingpage-Muster des Skills passen nicht zu einem Arbeitswerkzeug;
der mintfarbene Hintergrund wurde durch neutrales Papier ersetzt. Deshalb wurde das Skill-Ergebnis nicht als
`design-system/MASTER.md` gespeichert – maßgeblich ist dieser Abschnitt.

## 7. Zustände

| Zustand | Verhalten |
| --- | --- |
| Laden | „Lage wird geladen …“ + Platzhalterzeilen, bis Aufträge und Freigaben einmal geantwortet haben |
| Leer | „Alles entschieden.“ (grün) bzw. ehrliche Leertexte je Liste; leeres Gespräch mit drei Einstiegen |
| Teilweise nicht erreichbar | Liste bleibt, darunter „Nicht geprüft, weil nicht erreichbar: …“ |
| Offline | Hinweis an der Befehlszeile, roter Fuß der Navigation, Zustands-Chip „Offline“, letzter Stand bleibt |
| Fehler | Sprachfehler als Meldung mit Stufe; fehlgeschlagene Jobs als rote Einträge mit „Ansehen und wiederholen“ |
| Nur Ansicht (Beobachter) | Eingaben gesperrt mit Begründung, keine Vorschläge, kein Abbrechen |
| Erfolg | Job wandert von „Läuft gerade“ nach „Zuletzt fertig“ mit Ergebnisauszug |

## 8. Technische Erkenntnisse

- Keine Backend-Änderung: alles nutzt vorhandene Endpunkte (`/api/approvals`, `/api/agenten`, `/api/orders`,
  `/api/anrufe`, `/api/content`, `/api/calendar`, `/api/automations`, `/api/verbindungen`).
- Neu: `command/today.ts` (reine Funktionen `buildQueue`, `buildSuggestions`), `command/TodayView.tsx`.
- Geändert: `views/CommandView.tsx` (Navigation, Kopfzeile, mobile Leiste, `⌘K`), `command/CommandCenter.tsx`
  (Aufbau; neue Props `jobs`, `jobsError`, `busyJob`, `onOpenJob`, `onCancelJob`, `focusSignal`; entfallen:
  `capabilityError`, `onStartCapability`, `onToggleSkill`, `capabilityBusy`, `onCapabilityAction`, `onOpenOrder`).
- CSS-Variablen `--cc-blue*`/`--cc-navy*` heißen aus Kompatibilität weiter so, tragen aber Petrol bzw. Tinte.
- Neue Abhängigkeit: `@fontsource/ibm-plex-sans` (lokal gebündelt, kein CDN).
- Vorher schlug `app.test.tsx` fehl (erwartete alte Navigationsnamen); der Test ist auf die neue Navigation angepasst.

## 9. Signale, ob der Ablauf besser funktioniert (noch nicht gemessen, keine Zielwerte)

- Zeit von „Freigabe erstellt“ bis „entschieden“ (`erstellt` und Entscheidungszeit liegen vor).
- Anteil der Jobs, die ohne Fehlschlag oder Abbruch enden (`/api/agenten`).
- Anzahl Einträge in „Braucht dich“ am Tagesende.
- Übergaben pro Tag aus Befehlszeile/Command Center (bräuchte ein Ereignis im vorhandenen Aktionsprotokoll).

## 10. Abnahmekriterien

- [x] Startseite nennt die Lage in einem Satz und zeigt genau eine priorisierte Liste.
- [x] Auftrag ist von der Startseite ohne Navigation übergebbar (Feld + Enter) und landet im Command Center.
- [x] Jede Zeile in „Braucht dich“ führt mit einem Klick zum passenden Ziel (Freigabe, Job, Auftrag …).
- [x] Command Center zeigt Gespräch, wartende Freigaben und laufende Jobs; Messwerte nur in der Diagnose.
- [x] Keine erfundenen Werte: ohne Daten „–“ oder ein Leertext; nicht erreichbare Quellen werden genannt.
- [x] Beobachter können nichts auslösen.
- [x] 375, 768 und 1440 px ohne horizontales Scrollen geprüft.
- [x] Typprüfung, 94 Tests und Build laufen durch.

## 11. Offen

- Live-Zustände „Hört zu / Denkt / Spricht“, ein laufender Job und eine echte Freigabe wurden nicht im Browser
  ausgelöst (das hätte echte Aufträge an den laufenden Jarvis gesendet); sie sind durch Komponententests abgedeckt.
- Die Bereiche unter „Arbeit“ und „System“ sind nur umgefärbt, nicht neu gestaltet.
- Die Vollbild-Ansichten und die Sprachbühne nutzen weiter das dunkle Amber-Schema.
- Kein dunkles Schema für die Arbeitsoberfläche.
