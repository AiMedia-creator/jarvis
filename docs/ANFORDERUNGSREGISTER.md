# Jarvis – Anforderungsregister

Stand 10.11.2026, 23:45 Uhr · Branch `skill-plattform` · HEAD `c1eacad` plus uncommittete Änderungen dieser Sitzung (nichts committet, kein Remote).

Status-Legende: **A** umgesetzt und in dieser Sitzung selbst im Browser auf http://localhost:8080 geprüft · **B** umgesetzt, aber nicht live prüfbar (Zugang fehlt) · **C** teilweise · **D** zurückgestellt (mit Grund) · **E** blockiert (fehlender Zugang/Entscheidung von dir).

## Verifizierte Ausgangslage (Abschnitt 2 des Auftrags)

| Prüfpunkt | Befund |
|---|---|
| Verzeichnis, Branch, HEAD | `/Users/omar/Desktop/jarvis`, `skill-plattform`, `c1eacad`. Kein Git-Remote. |
| Uncommittet zu Beginn | Nur `jarvis/tools/google.py` (3 Zeilen: Client-Datei nicht auf sich selbst kopieren). Erhalten. |
| Laufende Server | Java-Dashboard **8080**, Python-Bridge **8766**, alte Oberfläche **8765** (leitet um). Port 4173 lief **nicht** (nur Dev-Server-Eintrag in `.claude/launch.json`). |
| MCP in `~/.claude.json` | Projekt `/Users/omar/Desktop/jarvis`: `jarvis-lesend`, stdio, `python -m jarvis.mcp_server`. Global: `voicestudio` (HTTP, in dieser Sitzung nicht erreichbar). |
| CLAUDE.md / AGENTS.md | Nicht vorhanden. |

## Anforderungen

| # | Anforderung (Abschnitt) | Status | Fundstelle · Änderung · Prüfergebnis |
|---|---|---|---|
| 1 | Skills inventarisieren und gezielt nutzen (1) | **C** | Genutzt: `anthropic-skills:built-in-browser`, `code-review` (7 Befunde, alle behoben). Geladen, aber nicht durchführbar: `office-hours` (verlangt deine Antworten, verbietet Umsetzung), `security-review` (bricht ohne Git-Remote ab). Nicht genutzt: gstack `design-review`/`qa` – siehe Abschlussbericht. |
| 2 | Übersicht einfach, vertriebsorientiert (3) | **A** | `dashboard/src/views/CommandView.tsx`: zwei Leitkarten („Heute am wichtigsten“ mit nachvollziehbarer Regel, „Nächste Umsatzaktion“ mit Grund, Frist, Quelle), darunter sechs Karten. KPI-Zeile entfernt. Geprüft bei 1280×800 und 375×812. |
| 3 | Klare Navigation (3) | **A** | Vier Gruppen: Heute · Arbeit · Agenten-System · Mehr. Sprach-/Suchbegriffe für neue Bereiche ergänzt. |
| 4 | Sales-Pipeline, Anrufe und Follow-ups (3, 5) | **A** (Ansicht) | `CallsPanel.tsx` mit drei Reitern. Neue Kennzahlen in `jarvis/anrufe.py` (ohne nächsten Schritt, Nummer privat/ungeklärt, ohne Kontaktquelle). Nur Leerzustand prüfbar: 0 Anrufe erfasst. |
| 5 | Lead-/Entscheiderrecherche, Cold-Call-Vorbereitung, Rollenspiel (5) | **A**/**C** | Neue Skills `lead-recherche`, `cold-call-vorbereitung`; Start als echter Job aus „Vorbereiten und üben“. Skill-Jobs live geprüft mit anderen Skills (siehe 9); Lead-Recherche selbst **nicht** gestartet (bräuchte eine echte Zielfirma von dir). |
| 6 | Kontaktquelle, Abrufzeit, private Nummern kennzeichnen; Vorgaben Deutschland (5) | **A** | Anrufzeile zeigt Quelle, Erfassungszeit, Warnhinweis. Rechtsrahmen-Karte (UWG § 7, § 7a). Keine Rechtsberatung. |
| 7 | Gmail-Entwürfe, HubSpot-Vorschau (5, 11) | **B** | Unverändert über Freigabe-Schicht. Gmail-Scope live bestätigt; kein Entwurf erzeugt (keine externe Aktion ohne Auftrag). HubSpot: kein Token. |
| 8 | Business-/Lebenscoach auf echten Daten (5) | **A** | Skill `wochen-coaching`; als Job gestartet und per Knopf abgebrochen (Abbruch-Test). Vollständiger Lauf nicht abgewartet. |
| 9 | Agenten-Center an echte Jobs gebunden (4, 10) | **A** | Neu: `AgentCenter.tsx`, `/api/agenten`, `uebersicht.jobs_view/agent_of/rollen`. Live: Job „content-auswertung“ gestartet → 7 echte Schritte → abgeschlossen (9 s, Tokens erfasst, Kosten „nicht erfasst“). Job „wochen-coaching“ abgebrochen → „Abgebrochen“, wiederholbar. 5 GitHub-Jobs (siehe 20). |
| 10 | „Agenten arbeiten“ kompakt in der Übersicht (4) | **A** | Zeigt nur laufende/wartende Jobs; sonst „Gerade arbeitet kein Agent“ mit letztem echten Job. |
| 11 | Neustart: Jobs wiederaufnehmen oder als unterbrochen markieren (4) | **A**/**C** | Vorhandene Logik (`jobs.restore`) unverändert; Anzeige „Unterbrochen (Neustart)“ neu. Live: laufender GitHub-Job beim Neustart → „Fehlgeschlagen: git fetch … -15“, wiederholbar. Der Restore-Pfad selbst (harter Absturz) wurde nicht ausgelöst. |
| 12 | Content-Workflow mit Hooks, Skript, Storyboard, Prompts, Varianten, Wiederverwertung (6) | **A** (Ansicht) / **C** | `ContentPanel.tsx`: Themenfeld → Hook-Job; je Eintrag Hook-Bewertungen, Skript-Auszug, Aktionen. Neue Skills `content-skript`, `content-auswertung`. Nur Leerzustand prüfbar (0 Einträge); kein Hook-Job mit echtem Thema gestartet. |
| 13 | Ab „Freigegeben“ entscheidest nur du; Publishing-Voraussetzungen (6) | **A**/**E** | Sperre in `content.py` unverändert. Keine Plattform-API angebunden; Oberfläche sagt das ausdrücklich. Voraussetzungen recherchiert (Abschlussbericht). |
| 14 | Gedächtnis: Archiv, Wissen, Index getrennt (7) | **A** | `jarvis/memory.py`: `begin_turn/finish_turn`, `log_utterance`, `fact_versions`, `update_fact`, `confirm`, `history`, `delete_episode`, `export`, `rebuild_index`, `center`. |
| 15 | Speicherstatus je Äußerung; Abbruch verliert nichts (7) | **A** | Live: Textzug im Command Center stand sofort als „pending“ im Archiv, nach der Antwort „completed“. Beim Start werden offene Äußerungen „interrupted“ (Wortlaut bleibt). |
| 16 | Erkannte Sprache fortlaufend speichern (7, 11) | **A** (Studio/Text) / **B** (Echtzeit) | Echtzeit-Gespräche landeten bisher **gar nicht** im Archiv. Neu: `/api/transkript` + Warteschlange im Browser (`realtime.ts`). Endpunkt live geprüft (speichern, Duplikat, Antwort anhängen, löschen). Echte Echtzeit-Sitzung nicht prüfbar: ElevenLabs-Schlüssel ohne Agents-Recht. |
| 17 | Kein Roh-Audio dauerhaft (7) | **A** | Nur Text wird gespeichert; Hinweis im Gedächtnis-Center. Code speichert kein Audio (unverändert). |
| 18 | Quellen, Zeitstempel, Versionsverlauf, Widersprüche (7) | **A** | Live im Browser: Eintrag angelegt → korrigiert → bestätigt → Verlauf zeigt alte Fassung → gelöscht (Suche danach leer). |
| 19 | Gedächtnis-Center: ansehen, suchen, korrigieren, bestätigen, exportieren, löschen (7) | **A** | `MemoryCenter.tsx`. Suche ohne Treffer zeigt „Nichts gefunden … erfindet keine Erinnerung“. Export-Knopf vorhanden, **nicht geklickt** (hätte Datei mit allen Gesprächen erzeugt). |
| 20 | Löschung erfasst abgeleitete Erinnerungen und Index (7) | **A** | `delete_episode` löscht abgeleitete Fakten (Feld `origin_episode`), FTS-Zeilen und Versionen. Geprüft gegen Wegwerf-Datenbank und live für Archiv-Eintrag. Alt-Fakten vor heute haben keine Herkunftsverknüpfung. |
| 21 | Ursache „Cloud Memory unzuverlässig“ finden und beheben (7, 8) | **A**/**E** | Vier belegte Ursachen: (1) MCP-Server war nur lesend; (2) Echtzeit-Gespräche wurden nie archiviert; (3) `merken` im Sprachkern schrieb in eine Datei statt ins Gedächtnis; (4) claude-mem-Observer ohne Anmeldung. 1–3 behoben, 4 braucht `/login` von dir. Zusätzlich sichtbar gemacht: Datei-Notizen des Modells (`~/.jarvis/memories`). |
| 22 | MCP Ende zu Ende: speichern → suchen → frische Sitzung → löschen (8) | **A** (stdio) | Test-Erinnerung „Quarzfalke-7319“: gespeichert über frischen MCP-Serverprozess; gefunden in zweitem frischen Prozess, über die in dieser Claude-Code-Sitzung verbundene MCP-Verbindung und im Dashboard; gelöscht; danach nirgends mehr gefunden. Schreibwerkzeug in Claude Codes Werkzeugliste erst nach Neustart der Claude-Code-Sitzung sichtbar. |
| 23 | Verbindungsrichtungen dokumentieren (8) | **A** | Karte „Verbindungen“ und Abschlussbericht. claude.ai → Jarvis: nicht verbunden, kein unsicherer Endpunkt gebaut. |
| 24 | Memory-Tools und GitHub-Repos vergleichen (9) | **C** | Recherche + drei Repos über Jarvis' eigene Fähigkeit in Quarantäne analysiert (`~/.jarvis/github/…`, Commit und Lizenz gespeichert). Khoj-Analyse durch Neustart abgebrochen. Kein fremder Code ausgeführt oder installiert. |
| 25 | Skill-Plattform: vier Dinge unterscheiden; Rollen prüfen (10) | **A** | Bereich „Skills und Automationen“; Rollenmatrix im Agenten-Center (10 Rollen, je mit Skill, Werkzeugen, echtem Status). Keine Schein-Agenten angelegt. |
| 26 | Google-Status aus echten Scopes (11) | **A** | `jarvis/verbindungen.py`: tokeninfo-Abfrage. Live: Gmail, Kalender, Drive, Aufgaben, Kontakte erteilt. |
| 27 | ElevenLabs sicher prüfen, 401 untersuchen (11) | **A** | Live: Schlüssel gültig (Konto 200), aber `voices_read` und `convai_read` fehlen → das erklärt den 401 beim Echtzeit-Start. Fehlermeldung in `eleven_agent.py` unterscheidet jetzt „ungültig“ von „Recht fehlt“. Sprachausgabe nicht getestet (würde Guthaben kosten). |
| 28 | Voice-Abbrüche über Minuten (11) | **C** | Aus Logs belegt: 17× Anthropic „usage/spend limit“ (20.10.–1.11.), 10× 400 wegen verwaistem Werkzeugaufruf, 3× Verbindung. Kein einziges Echtzeit-Ereignis protokolliert. Neu: `_repair_history` entfernt verwaiste Werkzeugaufrufe vor jedem Zug. Kein Live-Sprachtest. |
| 29 | Skill-Jobs nicht an Voice gebunden; keine Doppelaktionen (11) | **A** (aus Code + Jobtest) | Unverändert: eigener Thread-Pool, Idempotenz über `request_id`/Policy-Fenster. Transkript-Wiederholung ist über `ref` idempotent (live geprüft). |
| 30 | Design, Motion, Barrierefreiheit (12) | **A**/**C** | Farben `#3F78B5`/`#172B43` bestätigt und beibehalten; Linktext auf `#2F6195` abgedunkelt (Kontrast). Eine Animation (Puls nur bei laufendem Job), bei reduzierter Bewegung aus. Tastatur: native Buttons/Labels; kein Screenreader-Test. |
| 31 | Weltkarte (12) | **D** | Geprüft, nicht verändert: ehrlicher Einrichtungszustand, Orte inkl. Wall Street und London, Ersatzkarte gekennzeichnet. Ohne Mapbox-Token nichts sinnvoll zu verbessern. |
| 32 | Mikrofonleiste bei 1024×560 (14) | **A** | Gemessen: alle Knöpfe y ≤ 554, Höhe 40 px, nichts außerhalb, kein Querscrollen. |
| 33 | Leere, Fehler-, Freigabe-, Verbindungszustände (14) | **A**/**C** | Leer: Vertrieb, Content, Freigaben, Suche. Fehler: fehlgeschlagener Job mit Grund, ungültige ID (404/Hinweis), ElevenLabs unvollständig. Freigabe-Zustand: keine offene Freigabe vorhanden, daher nur „Keine offenen Freigaben“ gesehen. |
| 34 | Tools, Kosten, Zugänge (13) | **C** | Liste im Abschlussbericht; Preise aus Websuche (Drittquellen), vor Abschluss auf den Anbieterseiten prüfen. |
| 35 | Keine neuen Tests, keine Testsuiten (14) | eingehalten | Nur Kompilieren/Typprüfung/Build und ein Wegwerf-Prüfskript gegen eine temporäre Datenbank. Bestehende Tests **nicht** ausgeführt – ob `tests/test_memory_*.py` mit den Gedächtnis-Änderungen noch grün sind, ist daher ungeprüft. |

## Offene Punkte und bekannte Grenzen

- Voraussetzung für einen persönlichen Umsatzplan: Angebot, Preis, Zielgruppe und Leitfaden sind in keinem Vertriebsprojekt hinterlegt (0 Projekte).
- Nichts ist committet. Die Initial-Historie enthält laut früherer Notiz einen fest eingetragenen Schlüssel – vor einem Push bereinigen.
- `ANTHROPIC`-Preise sind nicht konfiguriert (`preise`), deshalb zeigt das Agenten-Center Tokens, aber keine Kosten.
- Der MCP-Eintrag heißt weiter `jarvis-lesend`, obwohl der Server jetzt Erinnerungen speichern kann. Umbenennen oder mit `JARVIS_MCP_NUR_LESEN=1` wieder rein lesend betreiben.
