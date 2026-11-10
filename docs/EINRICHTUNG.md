# Jarvis – Einrichtung: was nur du erledigen kannst

Stand 10.11.2026. Alles, was ohne Zugangsdaten, Kontobesitz oder Zahlung ging, ist gebaut (siehe `docs/designs/jarvis-vertriebssystem-phase1.md`, Abschnitt Umsetzungsstand). Die folgenden Punkte brauchen dich. Reihenfolge = Nutzen.

## Sofort (jeweils wenige Minuten)

| # | Aufgabe | Warum | Wie |
|---|---|---|---|
| 1 | **Google neu verbinden** | Die Google-Verbindung ist abgelaufen oder widerrufen (Test: „Die Google-Verbindung ist abgelaufen …“). Der Kalender im Dashboard liefert deshalb 502, Gmail-Entwürfe und Termine gehen nicht. | Im Terminal: `jarvis` beenden ist nicht nötig. Befehl: `~/.jarvis/venvs/v3/bin/python -m jarvis --google-verbinden ~/.jarvis/google_client.json` (bei Google alle Häkchen setzen). Falls der Befehl andere Argumente verlangt: `python -m jarvis --help` |
| 2 | **Neuen ElevenLabs-Schlüssel mit Berechtigung „ElevenLabs Agents“ anlegen** | Das Protokoll meldet 401 beim Vorbereiten der Echtzeit-Sitzung. Dadurch bleibt vermutlich die Höchstdauer von 10 Minuten beim Anbieter bestehen (Verdacht auf deinen Stimm-Abbruch, nicht bewiesen). | elevenlabs.io → Developers → API Keys → neuer Schlüssel, Berechtigung Agents aktivieren. Danach `ELEVENLABS_API_KEY` in `~/.zshrc` ersetzen und `jarvis-start.sh neustart` ausführen. Schlüssel nicht in den Chat posten. |
| 3 | **HubSpot-Token (oder Entscheidung dagegen)** | Ohne Token kann Jarvis keine CRM-Daten lesen oder Notizen vorschlagen. Die Anruf-Karte und der Anruf-Ablauf laufen auch ohne. | HubSpot → Einstellungen → Integrationen → Private Apps → App mit minimalen Rechten (Kontakte/Deals lesen, Notizen/Aufgaben schreiben) → Token als `HUBSPOT_TOKEN` in `~/.zshrc`. **Vorher beim Kunden klären, ob du sein CRM anbinden darfst.** |

## Entscheidungen

| # | Frage | Meine Empfehlung |
|---|---|---|
| 4 | Welches Projekt/CRM wird das erste echte Testprojekt? | Das Projekt, in dem du dein CRM selbst verwalten darfst. Im Dashboard „Anrufe“ → „Jarvis fragen“ legt es an. |
| 5 | Diktat lokal (Whisper, kostenlos) oder Cloud (Scribe, ca. 0,22 $/h)? | Lokal starten. |
| 6 | Privates GitHub-Repo anlegen? | Ja. Es gibt keinen Schlüssel in der Git-Historie (geprüft). Du brauchst dafür `gh` (Homebrew: `brew install gh`) und `gh auth login`; danach lege ich das private Repo an und pushe ohne Force-Push. |
| 7 | Echtzeit-Stimme nur gezielt nutzen? | Ja: ElevenLabs Agents kosten 0,08 $/Min plus LLM; eine Stunde täglich wären etwa 105 $ im Monat. |

## Claude Code mit Jarvis-Daten verbinden (optional)

`jarvis/mcp_server.py` ist ein **lesender** lokaler MCP-Server (kein Port, keine Schreibwerkzeuge, Zugriffsprotokoll `~/.jarvis/mcp-zugriffe.jsonl`). Registrierung in Claude Code (deine Entscheidung, ändert deine Claude-Konfiguration):

```bash
claude mcp add --transport stdio jarvis-lesend -- ~/.jarvis/venvs/v3/bin/python -m jarvis.mcp_server
```

Hinweis (laut Anthropic-Hilfe, abgerufen 10.11.2026): Claude.ai im Web kann nur **öffentlich erreichbare Remote-Server** anbinden, keine lokalen. Eine automatische Übernahme aller Claude-Chats ins Jarvis-Gedächtnis gibt es nicht; dafür bleibt ein expliziter Speicherbefehl. Claude.ai-Anbindung wäre ein eigenes Projekt (öffentlicher Server mit Anmeldung) und ist nicht empfohlen, bevor es einen Bedarf gibt.

## Später, nur wenn du es willst (kostet Geld oder Konten)

| Posten | Was dafür nötig ist |
|---|---|
| GoHighLevel / Close anbinden | API-Schlüssel je Kunde; ich prüfe vorher die offiziellen API-Dokus. Noch kein Code vorhanden. |
| Mapbox-Weltkarte | Mapbox-Konto und öffentliches Token (50.000 Ladevorgänge/Monat frei); Token in `dashboard/.env.local` (nicht einchecken). |
| KI-Video / Schnitt | Ein Tool erst testen (Preise stammen aus Blogs, nicht aus offiziellen Quellen). Kein Abo ohne deine Zusage. |
| TikTok/Instagram/YouTube automatisch posten | Nicht empfohlen: TikTok erlaubt ungeprüften Apps nur private Beiträge; Instagram/YouTube nicht geprüft. Manuell posten, Jarvis liefert Hook, Skript, Titel, Hashtags. |
| n8n | Erst wenn der eigene Scheduler eine Automation nicht trägt. Self-Host ohne genannte Lizenzgebühr, Cloud ab 20 €/Monat. |

## Was du testen sollst (wirkt nur mit echter Nutzung)

1. Ein Projekt anlegen, dann nach den ersten 20 Anrufen pro Tag die Nacharbeitszeit pro Anruf stoppen (Ausgangswert).
2. Nach einem Anruf diktieren: „Anruf bei Firma X, Anna Meier, Ergebnis Termin, nächster Schritt Mail bis Freitag.“ Prüfen: Eintrag unter „Anrufe“, Auftrag im Kanban, CRM-Status „offen“.
3. Rückmeldung, welche Ergebnis-Kategorien fehlen oder stören.
