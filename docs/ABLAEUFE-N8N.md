# Geplante Abläufe und n8n (Etappe 4 des Entwicklungsplans)

Stand: 18.10.2026.

## Ein Scheduler statt Coach-Schleife

`jarvis/scheduler.py` ist die **einzige** Scheduler-Schleife des Prozesses (gestartet/gestoppt von der Python-Brücke).
Die frühere Coach-Schleife (`coach.start_loop`) gibt es nicht mehr; `jarvis/coach.py` enthält nur noch die
Ablauf-Definitionen.

- **Lauf-Journal** `scheduled_runs` (in `memory.sqlite3`): stabiler Schlüssel je Lauf (z. B. `coach:2026-10-19`,
  `termin_vorbereitung:<event-id>:<beginn>`), Status getrennt: PLANNED, STARTED, SUCCEEDED, FAILED, UNCLEAR.
  Versuche, Lease-Besitzer und -Ablauf, Heartbeat, Job-ID, Graph-Lauf, Fehler, Ergebnis.
- **Kein Doppelstart:** Der Schlüssel ist eindeutig; Übernahme nur atomar per Lease (getestet mit vier gleichzeitigen
  „Prozessen“ und mit Neustart).
- **Fehler sind nie Erfolg:** Provider-Fehler, leere oder abgebrochene Ergebnisse -> FAILED. Interne Abläufe
  (nur lesen/entwerfen) werden begrenzt erneut versucht (Standard 2 Versuche, frühestens nach 15 Minuten); danach
  bleibt der Lauf FAILED (Dead-Letter, sichtbar in `ablaeufe_status`).
- **Neustart/Ruhezustand:** abgelaufene Leases -> FAILED („Unterbrochen“), danach begrenzter neuer Versuch.
  Ein ausgeschalteter oder schlafender Mac führt nichts aus; nach dem Aufwachen holt die Schleife fällige Läufe
  desselben Tages nach.
- **Graph:** Agent-Abläufe laufen als Hintergrund-Auftrag durch den LangGraph-Graphen; Job und Lauf tragen die
  Graph-Lauf-ID. Der Hinweis auf ein fertiges Briefing erscheint einmal im Zustandsvermerk.
- **Migration:** eine vorhandene `~/.jarvis/coach.json` wird übernommen und als `coach.json.migriert` behalten
  (nicht gelöscht).

## Abläufe

| Schlüssel | Standard | Was | Werkzeug-Rahmen |
|---|---|---|---|
| `coach` | **an** | werktags ab 07:30 Briefing zu Terminen, Aufgaben, Deals, Aufträgen | nur lesen |
| `fristwarnung` | aus | täglich 08:00 überfällige/bald fällige Aufträge, **ohne KI** | – |
| `mail_zu_auftrag` | aus | alle 3 h (werktags 7–20 Uhr) Kundenanliegen aus Mails als Auftragskarten | Mails lesen, Aufträge anlegen |
| `lead_followup` | aus | täglich 09:00 ruhende Leads; Follow-up-Mails nur als **Vorlage** | lesen + `mail_senden` (nur Vorlage) |
| `termin_vorbereitung` | aus | für Termine mit Teilnehmern in den nächsten 20 h ein Vorbereitungsbriefing | nur lesen |

Einschalten in `~/.jarvis/ablaeufe.json`, z. B.:

```json
{"fristwarnung": {"aktiv": true}, "mail_zu_auftrag": {"aktiv": true, "alle_stunden": 2},
 "coach": {"uhrzeit": "07:00", "wochenende": false}}
```

Der Werkzeug-Rahmen wird im Agenten **erzwungen** (nur erlaubte Werkzeuge sind sichtbar; ein anderer Aufruf wird
abgelehnt). Hintergrund-Aufträge können nie freigeben; Außenwirkung bleibt Vorlage + Einzel-Freigabe.
Nachbereitung von Terminen ist noch nicht als eigener Ablauf umgesetzt.

## n8n (`jarvis/tools/n8n.py`)

- Registry nur in `~/.jarvis/n8n.json` (Jarvis kann sie nicht ändern). Neue optionale Felder: `eingabe`
  (Pflichtfelder und Typen), `antwort` (`angenommen` | `abschluss`), `status_url` + `status_header`
  (n8n-API für Ausführungsstatus), `signatur_geheimnis` (HMAC für ausgehende Aufrufe und Rückmeldungen).
- **Stabile Action-ID** (Workflow + Daten + Tag) als `Idempotency-Key`; **Ausgangsjournal** `n8n_outbox`
  (ohne Schlüssel/Header) wird VOR dem Senden geschrieben.
- Status: ACCEPTED (200/202 ohne Abschluss), CONFIRMED (Abschluss bestätigt durch Vertrag, `n8n_status` oder
  signierten Callback), FAILED (abgelehnt/nicht erreichbar – erneuter Versuch gefahrlos), UNKNOWN
  (Zeitüberschreitung, 5xx, Absturz beim Senden – **nie** blind wiederholen).
- Gleicher Aufruf am selben Tag wird nicht erneut gesendet. `n8n_status <aktion>` gleicht über die Ausführungs-ID ab.
- Rückmeldung: `POST http://127.0.0.1:8766/internal/n8n/callback` (nur Loopback), Body
  `{"action_id","workflow","status","execution_id","ergebnis"}`, Header `X-Jarvis-Signature: sha256=<HMAC>`.
  Falsche Signatur, unbekannte Aktion oder falscher Workflow werden abgelehnt.

## OS-Autostart (eingerichtet am 18.10.2026)

macOS schützt den Schreibtisch-Ordner: Ein launchd-Hintergrunddienst bekam beim Start "Operation not permitted"
und wurde wieder entfernt. Stattdessen startet eine kleine **Start-App** `~/Applications/Jarvis Autostart.app`
(unter „Bei der Anmeldung öffnen“, unsichtbar, ohne Dock-Symbol) Jarvis im Echtmodus ohne Browserfenster
(`jarvis-start.sh hintergrund`) und beendet sich. Den Zugriff auf den Schreibtisch hat macOS einmal abgefragt.

- Läuft Jarvis schon, tut die App nichts (kein zweiter Start; getestet). `jarvis` im Terminal öffnet dann nur die
  Oberfläche.
- Kein automatischer Neustart nach einem Absturz (dann `jarvis` eingeben). Schlafender/ausgeschalteter Mac: nichts läuft.
- Verwalten: `python3 scripts/autostart.py status|install|uninstall`. Logs: `~/.jarvis/logs/`.

## Tests (18.10.2026)

- Python 190 bestanden (u. a. `tests/test_scheduler_n8n.py`: einmal pro Tag auch nach Neustart, 4 gleichzeitige
  Prozesse -> 1 Lauf, Fehler nie Erfolg, begrenzte Wiederholung, Absturz-Aufräumen, coach.json-Migration, nur eine
  Schleife, Standard aus, Fristwarnung ohne KI, Werkzeug-Rahmen, n8n: 202 ≠ erledigt, kein Doppelversand,
  Zeitüberschreitung unklar ohne Wiederholung, nicht erreichbar -> gefahrlos erneut, Abschluss-Vertrag, 5xx unklar,
  Eingabeprüfung, Ausführungs-ID-Abgleich, Callback-Signatur und -Besitzer, keine Schlüssel im Journal,
  Freigabepflicht).
- Live (getrennte Testinstanz, echter Kalender und Claude): Coach-Briefing über Scheduler -> Hintergrund -> Graph
  SUCCEEDED, nur erlaubte Werkzeuge benutzt; Fristwarnung SUCCEEDED; nach Neustart kein zweiter Lauf.
- Nicht geprüft: echtes n8n (kein n8n eingerichtet). Autostart: Start per App getestet; der Start bei einer echten Anmeldung zeigt sich beim nächsten Login.
