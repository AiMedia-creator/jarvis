# Jarvis – Sicherheitsmodell

## Grundsätze
1. **Mensch entscheidet (Vorlage + Freigabe, `jarvis/approvals.py`):** Jede Aktion nach außen erzeugt zuerst eine
   Vorlage (WAITING_FOR_APPROVAL), die als Karte mit Empfänger, Inhalt und Zielsystem erscheint. Ausgeführt wird nur
   nach „Ja“ (Aufruf mit identischen Angaben) oder Klick auf „Senden“ – und nur, wenn genau diese Vorlage offen ist.
   Erledigte Vorlagen können nicht erneut ausgeführt werden (Duplikatschutz); Vorlagen verfallen nach 30 Minuten.
   Betroffen: `mail_senden`, `termin_erstellen`, `hubspot_notiz_erstellen`, `hubspot_aufgabe_erstellen`,
   `hubspot_mail_protokollieren`, `hubspot_mail_senden`.
   **Serverseitig erzwungen (seit 17.10.2026):** Ein „Ja“ per Stimme/Chat gilt nur in einem späteren Nutzerzug als dem,
   in dem die Vorlage entstand (kein Selbst-Freigeben durch das Modell, z. B. nach Prompt Injection). Hintergrund-Aufträge
   können nie freigeben. `JARVIS_FREIGABE_NUR_KLICK=true` erlaubt nur den Klick. Ausgeführte Aktionen stehen als Hash in
   `~/.jarvis/aktionen.jsonl`; eine identische neue Vorlage zeigt einen Duplikat-Hinweis. Details: `AUDIT-2026-10-17.md`.
2. **Fremde Inhalte sind Daten:** Mails, Webseiten, Telegram, CRM-Felder und Dateien werden markiert
   (`[MAIL-INHALT …]`, `[TELEGRAM …]` usw.) und laut Prompt nie als Anweisung ausgeführt.
3. **Nur lokal:** Server bindet an 127.0.0.1; Host-Header-Prüfung gegen DNS-Rebinding; POST nur mit Header
   `X-Jarvis: 1` (blockiert Fernsteuerung durch andere Webseiten; kein Berechtigungsnachweis, siehe Punkt 10).
4. **Keine Shell aus Modelltext:** Einziges Systemwerkzeug ist `programm_oeffnen` (`open -a NAME`, Sonderzeichen gesperrt)
   und `seite_oeffnen` (nur http/https).
5. **Telegram nur lesend, nur Kanäle:** private Chats werden gefiltert, Senden ist nicht implementiert.
6. **Audit-Log:** jeder Werkzeugaufruf landet in `~/.jarvis/audit.jsonl` (Zeit, Quelle, Werkzeug, gekürzte Argumente,
   Freigabe, Ergebnis/Fehler). Lange Texte werden gekürzt, Felder mit token/passwort/hash/secret entfernt.
7. **Abbrechen:** Stopp per Stimme („Stopp“), Esc, Reinreden oder `/api/stop`; die Antwort wird serverseitig abgebrochen.

## Browser-Agent (Browser Use) und gbrain – seit 11.11.2026
8. **Browser-Agent:** aus per Standard. Browser Use führt vom Modell geschriebenen Code aus und kennt keine Freigabe-Hooks;
   die Grenzen liegen deshalb außerhalb (seit 12.11.2026): zwei macOS-Sandboxen (Agent ohne eigenes Netz und ohne
   Benutzerdateien, Chrome nur über den Filterproxy), ein Netzfilter im Jarvis-Prozess (nur erlaubte Domains, keine
   Loopback-, privaten oder Metadaten-Adressen, nur GET/HEAD/OPTIONS) und ein LLM-Relay (kein Anthropic-Schlüssel im
   Agenten). `browser_recherche` liest nur. `browser_interaktion` (eigener Schalter `JARVIS_BROWSER_INTERAKTION`) klickt
   und tippt; alles mit Außenwirkung wird festgehalten und nur über `browser_absenden` nach einer gesonderten Freigabe
   gesendet, deren Karte Aktion, Website und Daten zeigt. Keine angemeldeten Profile. Details und Restrisiken:
   `BROWSER-AGENT.md`.
9. **gbrain:** aus per Standard, parallel zum SQLite-Gedächtnis, nur lesendes Zusatzwerkzeug. Export nur bestätigter
   Erinnerungen des eigenen Scopes; kein Import, wenn dabei Inhalte an einen Embedding-Anbieter gingen und der
   Cloud-Schalter aus ist. Details: `GEDAECHTNIS-GBRAIN.md`.
10. **CRM:** Leads und Anruf-Ergebnisse bleiben lokal. Kalendereinträge für Meetings entstehen nur als Vorlage.
    **Schreiben darf nur der Besitzer des Raums (seit 12.11.2026, serverseitig):** `X-Jarvis: 1` ist ein fester Header
    und kein Nachweis. Beim Beitritt gibt der Java-Server dem Besitzer eines Raums über dessen Steuerverbindung ein
    zufälliges Ticket (`owner_ticket` im WELCOME, 32 Byte, nur im Arbeitsspeicher des Fensters). Alle schreibenden
    `/api/leads*`-Aufrufe brauchen Session, erlaubten Origin, `X-Jarvis: 1` **und** `X-Jarvis-Room` + `X-Jarvis-Owner`.
    Der Server prüft das Ticket zeitkonstant gegen den Raum, verlangt dieselbe Session wie beim Beitritt und lehnt ab,
    sobald der Besitzer nicht mehr verbunden ist. Beobachter (zweites Fenster, `ops`) erhalten kein Ticket und können
    nur lesen. Restrisiko: Ohne Anmeldepflicht (`auth-required=false`, lokaler Standard) kann ein anderes lokales
    Programm selbst einen eigenen Raum öffnen und wäre dort Besitzer; der Schutz trennt Fenster und Rollen, er ersetzt
    keine Anmeldung.
11. **Command Center:** eigenes Fenster mit eigenem Raum. Aufträge aus dem Dashboard wandern über den lokalen
    Browser-Speicher dorthin (höchstens zwei Minuten gültig), nie über die Adresse.

## Secrets
| Secret | Ort |
|---|---|
| Anthropic-, ElevenLabs-, HubSpot-Schlüssel, Workspace-ID | `~/.zshrc` (Umgebung) |
| Google-Token, Telegram-Sitzung | `~/.jarvis/` (Rechte 600) |
| Telegram api_id/api_hash, Agent-ID | `~/.jarvis.json` (Rechte 600) |
Nie im Code, nie im Audit-Log. Secret-Scan über das Projekt: siehe `tests/test_secrets.py`.

## Bekannte Restrisiken
- Beim gesprochenen „Ja“ entscheidet weiterhin das Modell, ob ein Ja vorlag – aber nur für eine Vorlage, die du
  vorher gesehen hast. Strengste Variante (nur Klick) ist als Einstellung möglich, falls gewünscht.
- ElevenLabs-Echtzeit: Gesprächsinhalte und Werkzeugergebnisse laufen über ElevenLabs-Server.
- Telegram-Sitzung = Vollzugriff aufs Konto, falls die Datei gestohlen wird.
- Mehrere früher im Chat gepostete Schlüssel müssen rotiert werden.
