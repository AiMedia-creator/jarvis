# Jarvis – Anforderungsregister

Stand 10.11.2026, Branch `skill-plattform`. Status-Legende:
**A** umgesetzt und selbst im Browser geprüft · **B** umgesetzt, nicht live mit Zugangsdaten geprüft · **C** teilweise · **D** zurückgestellt (mit Grund) · **E** blockiert (fehlender Zugang).
„Selbst geprüft“ heißt: in dieser Sitzung im Browser auf http://localhost:8080 gesehen. Angaben aus dem früheren Bericht, die ich nicht erneut ausgeführt habe, sind als solche markiert.

## Verifizierte Ausgangslage
- Branch `skill-plattform`, Arbeitsbaum sauber, Commits `c8cac6b`, `dce26d2`, `6cce61a`, `f712558` vorhanden. Kein Git-Remote, `gh` nicht installiert.
- Jarvis läuft lokal auf **http://localhost:8080** (Java-Server liefert `dashboard/dist`).
- Google-Verbindung abgelaufen: Übersicht zeigt „Kalender nicht verfügbar“ mit Wiederverbindungshinweis; kein 502-Fake, keine erfundenen Termine.
- HubSpot: nicht verbunden (`/api/status`: `hubspot:false`).

## Anforderungen

| # | Anforderung | Status | Nachweis / Lücke |
|---|---|---|---|
| 1 | Übersicht mit nächster Umsatzaktion, Grund, Fälligkeit | **A** | Neue Karte „Nächste Umsatzaktion“ (leere Daten → „Erstes Gespräch dokumentieren“; fällige Follow-ups → Aktion mit Frist). Nichts erfunden. |
| 2 | Dienste-Status (verbunden/abgelaufen/nicht eingerichtet) | **A** | Karte „Verbundene Dienste“ (Kalender/Google, HubSpot, ElevenLabs, GitHub, n8n). Kopfzeile zeigt „Kalender nicht verfügbar“. |
| 3 | Pipeline, Anrufe, Follow-ups | **A** (Ansicht) / **C** (Daten) | Ansicht „Anrufe“ mit Kennzahlen, Pipeline, Projekten; echte Leere. „Anrufe ohne Ergebnis“ als Kennzahl fehlt noch. |
| 4 | Kalender, Termin-Detail | **C** | Ansicht vorhanden; ohne Google-Verbindung nur Fehlerzustand prüfbar. |
| 5 | Aufträge-Kanban | **A** | Ansicht lädt Aufträge über `/api/orders`. |
| 6 | Content-Workflow Idee → … → Auswertung inkl. **Hooks** | **A** | Spalte „Hooks“ ergänzt (Backend `content.py`, Fähigkeiten-Schema, UI), 10 Spalten im Browser geprüft. |
| 7 | Keine Veröffentlichung ohne Bestätigung | **A** (Logik, aus Code) | `JARVIS_MAX` = Review; darüber nur der Nutzer. Keine Plattform-API angebunden. |
| 8 | Skripte, Storyboards, Shot-Listen, Prompts für KI-Werkzeuge | **C** | Hooks-Bewertung und Content-Skill vorhanden (früherer Bericht); nicht erneut durchgespielt. |
| 9 | Social-Media-APIs (TikTok/IG/YT) | **E/D** | Nicht gebaut: erst Konten/App-Prüfung; Plattformregeln nicht neu recherchiert. |
| 10 | Gedächtnis (Archiv, Wissen, Index) | **C** | Vorhandene Gedächtnis-Ansicht und `/api/memory` (Fakten, Episoden, Konflikte) geprüft; Vergleich Obsidian/Khoj/Mem0/Eigenlösung und Transkript-Status-Anzeige **nicht** abgeschlossen. Empfehlung bis dahin: lokale Eigenlösung behalten. |
| 11 | Claude/MCP-Verbindung | **B** | Lesender MCP-Server `jarvis/mcp_server.py` vorhanden; Registrierung in Claude Code steht aus (siehe EINRICHTUNG.md). Claude.ai-Cloud-Chats landen nicht automatisch im Gedächtnis. |
| 12 | Command Center neu, Mikrofonbefehle erreichbar | **A** | Bei 1024×560 lag die Mikrofonleiste außerhalb des Bildes (y=570). Jetzt am unteren Rand fixiert, Fläche ≥ 40 px, Safe-Area; im Browser nachgemessen (alle Knöpfe y ≤ 554). Eigenes Audio-Visual/Orb-Ersatz: bereits „Signalband“, nicht neu bewertet. |
| 13 | Voice-Abbruch nach Minuten | **C** | Ursache nicht belegt. Früherer Verdacht: 401 beim ElevenLabs-Agents-Schlüssel (Höchstdauer 10 min). Live-Sprachtest steht aus. |
| 14 | Skill-Plattform (`auftrag_anlegen`, `skill_starten`, `github_*`, Jobs) | **A**/**B** | `/api/faehigkeiten` liefert `auftrag_anlegen`; Command-Center-Panel „Skill-Plattform“ mit Aufträge/Bibliothek sichtbar. GitHub-Suche live nicht getestet (Netz/Token). |
| 15 | Agenten mit Grenzen | **C** | Übersicht `/api/automations` listet Agenten mit Status; Rechte-Matrix pro Agent nicht neu geprüft. |
| 16 | browser-use / n8n | **D** | Installation nicht verifiziert (nicht geprüft, daher keine Doppelinstallation). n8n: vorhandener Scheduler genügt vorerst. |
| 17 | Gmail nur Entwürfe, nie senden | **B** | Aus Architektur (Freigabe-Schicht); wegen abgelaufenem Google-Zugang nicht live prüfbar. |
| 18 | HubSpot mit Vorschau vor Schreiben | **B** | Vorschau-Status `vorschau` in Anrufen; ohne Token nicht live prüfbar. |
| 19 | Privates GitHub-Repo | **E** | Kein `gh`, keine Anmeldung. Anleitung in `docs/EINRICHTUNG.md` (#6). |
| 20 | Weltkarte (Mapbox) | **D** | Ansicht „World View“ vorhanden; Mapbox-Token fehlt, Straßenkameras nicht verfügbar. |
| 21 | Browser-Prüfung, kleine Fenster | **A** | Übersicht, Anrufe, Content, Command Center bei 800×600 und 1024×560 geprüft. Kalender nur Fehlerzustand. |
| 22 | Einrichtungsliste | **A** | `docs/EINRICHTUNG.md` (vorhanden, aktuell); Ergänzungen im Abschlussbericht. |

## Bekannte Beobachtungen
- Das Eingabefeld im Command Center war „Nur Ansicht“, weil ein anderes Jarvis-Fenster die Steuerung hält. Mikrofon und Texteingabe dort deshalb nicht selbst getestet.
- „Ablauf Coach-Briefing: letzter Lauf unklar (Zeitlimit überschritten)“ ist ein echter Befund aus dem Scheduler; Ursache nicht untersucht.
- claude-mem (Sitzungsgedächtnis) konnte in dieser Sitzung nichts speichern (Observer-Anmeldung abgelehnt).
