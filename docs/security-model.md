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
   `X-Jarvis: 1` (blockiert Fernsteuerung durch andere Webseiten).
4. **Keine Shell aus Modelltext:** Einziges Systemwerkzeug ist `programm_oeffnen` (`open -a NAME`, Sonderzeichen gesperrt)
   und `seite_oeffnen` (nur http/https).
5. **Telegram nur lesend, nur Kanäle:** private Chats werden gefiltert, Senden ist nicht implementiert.
6. **Audit-Log:** jeder Werkzeugaufruf landet in `~/.jarvis/audit.jsonl` (Zeit, Quelle, Werkzeug, gekürzte Argumente,
   Freigabe, Ergebnis/Fehler). Lange Texte werden gekürzt, Felder mit token/passwort/hash/secret entfernt.
7. **Abbrechen:** Stopp per Stimme („Stopp“), Esc, Reinreden oder `/api/stop`; die Antwort wird serverseitig abgebrochen.

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
