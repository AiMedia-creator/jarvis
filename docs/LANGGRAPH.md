# LangGraph-Orchestrierung (Etappe 1–2 des Entwicklungsplans)

Stand: 18.10.2026. **Vorher gab es kein LangGraph im Projekt** (keine Imports, keine StateGraph-Definition, kein
Checkpointer, nicht in requirements.txt). Dies ist eine **kontrollierte Migration** der bisherigen 20-Runden-Schleife in
`jarvis/agent.py` auf einen aktiven LangGraph-Graphen.

## Was jetzt tatsächlich läuft

- `jarvis/graph.py` ist der **aktive Ausführer** jedes Agenten-Zugs (Sprache, Web/Dashboard, Text, Hintergrund).
  `Agent.ask()` bleibt die Fassade; Callbacks, Job-/Telemetrie-Verträge, NDJSON/WebSocket und Java-Brücke sind
  unverändert. Token-, Satz- und Schritt-Ereignisse fließen weiter direkt aus den Knoten, nicht erst am Graphende.
- Versionen (gepinnt): `langgraph==1.2.16`, `langgraph-checkpoint-sqlite==3.1.2` (API vor Einsatz geprüft:
  `StateGraph(context_schema=...)`, `interrupt`, `Command(resume={interrupt_id: wert})`, `Send`, `Overwrite`,
  `durability="sync"`).

## Graph

```
START -> validate_request -> load_context -> route_intent -> model_step <-> run_tools
      -> quality_check -> prepare_action -> await_approval (je Freigabe ein paralleler Zweig, interrupt)
      -> execute_or_reconcile -> verify_result -> persist_business_result -> respond -> END
      (step_limit bei Schrittgrenze; validate-Fehler direkt zu respond)
```

- `route_intent`: deterministische Einstufung (kein LLM): Dialog = höchstens 4 Modellrunden, Auftrag = 20.
  Rechte setzt weiterhin `policy.py` je Werkzeug durch.
- `quality_check`: deterministisch (leere Antwort, Erfolgsmeldung ohne ausgeführte Aktion -> Ereignis-Log).
- Noch **nicht** umgesetzt (nächste Etappe laut Plan): `plan_task`, ModelRouter, Spezialisten-Subgraphs
  (Inbox, Kalender, CRM, Auftragsmanager, Research, QA, Desktop). Die Zustandsfelder `plan`, `task_refs`,
  `evidence_refs`, `decision_ref`, `business_order_id` existieren, werden aber noch nicht befüllt.

## Freigaben: genau ein Ausführer

- **Ausführungsbesitzer ist der bestehende Freigabe-Dispatcher** (`approvals.py`): per Klick (`approve`) oder per
  "Ja" im nächsten Zug (Werkzeugaufruf mit `bestaetigt_vom_nutzer=true`, Zugbindung wie bisher).
- Der Graph führt nie selbst aus. `await_approval` pausiert per `interrupt` mit strukturierter Vorschau
  (Aktion, Ziel, Daten, Risiko, Ablaufzeit, Payload-Hash, Freigabe-ID). Nach dem Endstatus der Freigabe ruft
  `approvals.on_resolved` -> `GraphService.resolve()` auf; das prüft Thread, offenen Interrupt, Freigabe-ID,
  Endstatus und unveränderten Payload-Hash und setzt nur dann fort. `execute_or_reconcile` liest danach den
  tatsächlichen Status beim Dispatcher. Ein Resume ist keine Freigabe; Modelltexte können keinen Resume auslösen.
- Mehrere offene Freigaben: je ein paralleler Zweig (`Send`), einzeln über die Interrupt-ID adressiert;
  Ergebnisse über einen Reducer (`decisions`).
- Neue Eingabe bei offenem Interrupt: Der Zweig wird ausdrücklich als "überholt" abgeschlossen (LangGraph würde ihn
  sonst still verwerfen – geprüft). Die Freigabe-Karte bleibt offen und klickbar; der Dispatcher führt weiterhin
  genau einmal aus.

## Thread, Checkpoints, Gedächtnis

- `thread_id = installation:owner:kanal:gespraech`, serverseitig gebildet und validiert; Hintergrund-Aufträge
  (`bg-…`) und Sprachgespräche sind getrennte Threads. Parallele Läufe je Gespräch verhindert die bestehende
  Agent-Sperre.
- Checkpointer: `SqliteSaver` in **eigener Datei** `~/.jarvis/graph_checkpoints.sqlite3` (WAL, Rechte 0600,
  `JsonPlusSerializer(pickle_fallback=False)`), `durability="sync"`.
- Inhalt: nur JSON-Daten und ein **begrenzter, gekürzter Gesprächsauszug** (letzte 12 Nachrichten, je 1500 Zeichen,
  Werkzeugergebnisse nur als Verweis). Keine SDK-Objekte, Schlüssel, Audio, Thread-Objekte oder Mailboxen.
- Retention: beim Start je Thread die neuesten 40 Checkpoints behalten, Threads ohne offene Freigabe nach 14 Tagen
  löschen. `memory.sqlite3` (Fakten, Vorlieben, Lektionen, Episoden, Jobs, Freigaben) wird nicht angefasst.
- Nach einem Absturz startet ein Checkpoint **nie von selbst** einen Worker; der nächste Zug beginnt neu
  (getestet). Was beim Absturz gerade nach außen lief, markiert der Dispatcher weiterhin als UNKLAR.
  Keine "exactly once"-Garantie aus Checkpointing.

## Konfiguration und Rückweg

- `JARVIS_ORCHESTRATOR=legacy` (oder `"orchestrator": "legacy"` in `~/.jarvis.json`) schaltet auf die alte
  Schleife zurück (`Agent._legacy_turn`); pro Zug läuft genau ein Ausführer.
- Vollständiger Rückweg: Backup vor der Migration unter `~/.jarvis/backups/vor-langgraph-<zeit>/`
  (Projekt-ZIP + konsistente Kopie von `memory.sqlite3`, Integritätsprüfung "ok").
  `graph_checkpoints.sqlite3` kann gefahrlos gelöscht werden.
- Kein LangSmith/Cloud-Tracing: nichts wird exportiert (keine Tracing-Variablen gesetzt).

## Tests (lokal ausgeführt, 18.10.2026)

- Python: `~/.jarvis/venvs/v3/bin/python -m pytest -q` -> 170 bestanden, 2 übersprungen
  (inkl. `tests/test_graph.py`: Interrupt/Klick genau einmal, doppelter Klick, falsche ID, geänderter Payload,
  mehrere offene Freigaben, neue Eingabe bei offenem Interrupt, "Ja" im nächsten Zug ohne Doppelversand,
  Neustart mit offener Freigabe, Absturz ohne automatische Wiederholung, Budget, Schrittgrenze, Legacy-Rückweg,
  getrennte Threads, keine Schlüssel/SDK-Objekte im Checkpoint, Retention).
- Java: `./mvnw test` in `java-backend/` -> 21/21. Dashboard: `pnpm typecheck`, `pnpm test` -> 30/30.
- Live (echte Claude-API, echter Kalender): Frage mit Kalenderlesen über den Graphen; Termin-Vorlage pausiert als
  Interrupt, nach Ablehnung abgeglichen (Status "cancelled"), nichts angelegt.
- Nicht geprüft: Last mit vielen Nutzern (nicht vorgesehen), Langzeitbetrieb über Tage.
