"""LangGraph-Orchestrierung des Agenten (Migration der bisherigen 20-Runden-Schleife).

Graph je Nutzerzug (Thread = Gespräch, eindeutig nach Installation/Besitzer/Kanal/Gespräch):

    START -> validate_request -> load_context -> route_intent -> model_step <-> run_tools
          -> quality_check -> prepare_action? -> await_approval (interrupt, je Freigabe ein paralleler Zweig)
          -> execute_or_reconcile -> verify_result -> persist_business_result -> respond -> END

Grundsätze:
- Ein Ausführer: Aktionen nach außen führt ausschließlich der bestehende Freigabe-Dispatcher (approvals.py) aus,
  per Klick oder per "Ja" im nächsten Zug. Der Graph wartet (interrupt) und gleicht danach nur ab
  (execute_or_reconcile liest den Status), er führt nie selbst aus. Ein Resume ist keine Freigabe.
- Resume nur über GraphService.resolve(): prüft Thread, offenen Interrupt, Freigabe-ID, Endstatus der Freigabe und
  unveränderten Payload-Hash. Werte aus dem Modell oder einem Klangereignis können keinen Resume auslösen.
- Neue Eingabe bei offenem Interrupt: Der alte Zweig wird erst ausdrücklich als "überholt" abgeschlossen
  (LangGraph würde ihn sonst still verwerfen); die Freigabe-Karte bleibt offen und klickbar.
- Checkpoints (eigene Datei graph_checkpoints.sqlite3, 0600, kein Pickle) enthalten nur JSON-Daten und einen
  begrenzten, gekürzten Gesprächsauszug, keine SDK-Objekte, Schlüssel, Audio oder Mailboxen. Langzeitwissen bleibt in
  memory.sqlite3. Ein Checkpoint startet nach einem Absturz nie von selbst einen Worker.
- Token-Streaming, Sätze und Schritte laufen weiter direkt über die Callbacks des Agenten (Laufzeit-Kontext),
  nicht erst am Graphende. Abbruch und Budget greifen in jedem Knoten.
- Legacy-Rückweg: JARVIS_ORCHESTRATOR=legacy nutzt die alte Schleife; pro Zug läuft genau ein Ausführer.
"""

from __future__ import annotations

import datetime
import hashlib
import operator
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Annotated, Any, Callable, TypedDict

from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, Overwrite, Send, interrupt

from . import config

CHECKPOINT_FILE = "graph_checkpoints.sqlite3"
KEEP_CHECKPOINTS = 40          # je Thread
MAX_THREAD_AGE_DAYS = 14
DIRECT_STEPS = 4               # kurzer Dialog
TASK_STEPS = 20                # Auftrag (wie die bisherige Schleife)
RUN_TIMEOUT = 900.0            # Sekunden je Zug
CONTEXT_MESSAGES = 12
CONTEXT_CHARS = 1500
_THREAD_PART = re.compile(r"^[A-Za-z0-9_.@-]{1,80}$")
_APPROVAL = re.compile(r"VORLAGE erstellt, noch NICHT ausgeführt \(Freigabe-ID (\w+)\)")
FINAL_APPROVAL = {"COMPLETED", "FAILED", "CANCELLED", "UNCLEAR"}


def _merge(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class TurnState(TypedDict, total=False):
    # Identität und Bezüge
    installation_scope: str
    owner_scope: str
    conversation_id: str
    thread_id: str
    run_id: str
    technical_job_id: str
    business_order_id: str | None
    channel: str
    generation_id: str
    status: str
    # Routing
    text: str
    intent: str
    risk: str
    route: str
    autonomy_mode: str
    max_steps: int
    deadline: float
    # Plan und Belege (Plan/Spezialisten folgen in der nächsten Etappe; hier nur Felder)
    plan: list
    task_refs: list
    evidence_refs: list
    decision_ref: str | None
    # Begrenzter Gesprächsauszug (gekürzt, ohne Tool-Rohdaten)
    messages: list
    # Ablauf
    step_count: int
    last_stop: str
    answer: str
    tool_result_refs: Annotated[list, operator.add]
    pending_action_refs: list
    approval_id: str | None
    action_payload_hash: str | None
    decisions: Annotated[dict, _merge]
    action_previews: dict
    execution_status: dict
    errors: Annotated[list, operator.add]
    quality: list
    budget_limits: dict
    usage_totals: dict
    cancellation_ref: str | None


@dataclass
class TurnContext:
    """Nicht persistierter Laufzeit-Kontext: Agent, Callbacks, offene Werkzeugaufrufe."""
    agent: Any
    on_sentence: Callable[[str], None]
    on_tool: Callable[[str], None]
    on_token: Callable[[str], None]
    on_step: Callable[[dict], None]
    start: int = 0
    splitter: Any = None
    answer: list = field(default_factory=list)
    calls: list = field(default_factory=list)
    final_text: str | None = None


# --- Hilfen ---------------------------------------------------------------------------------------------

def thread_id(installation: str, owner: str, channel: str, conversation: str) -> str:
    parts = [installation, owner, channel, conversation]
    if not all(_THREAD_PART.match(str(p or "")) for p in parts):
        raise ValueError("Ungültige Thread-Bestandteile")
    return ":".join(parts)


def payload_hash(tool: str, key: str) -> str:
    return hashlib.sha256(f"{tool}\n{key}".encode()).hexdigest()[:32]


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for block in content or []:
        kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", "")
        if kind == "text":
            out.append(block.get("text", "") if isinstance(block, dict) else getattr(block, "text", ""))
        elif kind == "tool_use":
            out.append(f"[Werkzeug {block.get('name') if isinstance(block, dict) else getattr(block, 'name', '')}]")
        elif kind == "tool_result":
            out.append("[Werkzeugergebnis]")
    return " ".join(x for x in out if x)


def _bounded_context(messages: list) -> list:
    """Datensparsamer Auszug für den Checkpoint: letzte Nachrichten, gekürzt, ohne Werkzeug-Rohinhalte."""
    return [{"role": m.get("role"), "text": _text_of(m.get("content"))[:CONTEXT_CHARS]}
            for m in messages[-CONTEXT_MESSAGES:] if isinstance(m, dict)]


def _ctx(runtime: Runtime) -> TurnContext:
    return runtime.context


def _check(ctx: TurnContext, state: TurnState) -> None:
    ctx.agent._check()  # Abbruch (Nutzer/neue Generation) -> Cancelled
    if time.time() > state.get("deadline", float("inf")):
        raise TimeoutError("Zeitlimit für diesen Auftrag erreicht")


_TASK_WORDS = re.compile(r"\b(schreib|erstell|send|bewert|analysier|plan|recherch|such|finde|lies|öffne|oeffne|"
                         r"ordne|räum|raeum|termin|mail|lead|angebot|bewerbung|drive|workflow|n8n|aufgabe|kalender|"
                         r"deal|kunde|strategie|website|landingpage|go\b)", re.I)


def classify(text: str, channel: str) -> dict:
    """Deterministische Vorab-Einstufung (kein LLM): bestimmt Schrittbudget und Protokollierung, nicht die Rechte.
    Die Rechte setzt weiterhin policy.py je Werkzeug durch."""
    t = str(text or "")
    task = bool(_TASK_WORDS.search(t)) or len(t) > 280 or channel == "hintergrund"
    risk = "external" if re.search(r"\b(send|schick|termin|freigeb|bezahl|überweis|ueberweis|workflow)", t, re.I) \
        else "read"
    intent = ("akquise" if re.search(r"\b(lead|upwork|bewerbung|proposal|job)\b", t, re.I) else
              "drive" if re.search(r"\bdrive\b", t, re.I) else
              "automation" if re.search(r"\b(n8n|workflow|automatisier)", t, re.I) else
              "strategie" if re.search(r"\b(strategie|wachstum|preis|skalier|saas)", t, re.I) else
              "auftrag" if task else "dialog")
    return {"intent": intent, "risk": risk, "route": "task" if task else "direct",
            "max_steps": TASK_STEPS if task else DIRECT_STEPS,
            "autonomy_mode": "hintergrund" if channel == "hintergrund" else "dialog"}


# --- Knoten ---------------------------------------------------------------------------------------------

def validate_request(state: TurnState, runtime: Runtime) -> dict:
    text = str(state.get("text") or "")
    if not text.strip():
        return {"status": "failed", "errors": ["leere Anfrage"]}
    if len(text) > 20000:
        return {"status": "failed", "errors": ["Anfrage zu lang"]}
    return {"status": "running", "deadline": time.time() + RUN_TIMEOUT}


def load_context(state: TurnState, runtime: Runtime) -> dict:
    ctx = _ctx(runtime)
    ctx.agent._append_user_turn(state["text"])  # Datum, Zustandsvermerk, Execute Mode, Hinweise (wie bisher)
    ctx.start = ctx.agent._graph_start
    return {"messages": _bounded_context(ctx.agent.messages)}


def route_intent(state: TurnState, runtime: Runtime) -> dict:
    return classify(state["text"], state.get("channel", ""))


def model_step(state: TurnState, runtime: Runtime) -> dict:
    ctx = _ctx(runtime)
    _check(ctx, state)
    agent = ctx.agent
    agent._rounds += 1
    final = agent._stream_step(ctx.splitter, ctx.answer, ctx.on_token)
    ctx.splitter.flush()
    steps = state.get("step_count", 0) + 1
    if final is None:  # Wiederholung ohne Caching/Effort oder unlesbare Argumente
        return {"step_count": steps, "last_stop": "retry"}
    agent._account(final)  # Budget -> BudgetExceeded
    usage = {"tokens": getattr(agent, "_tokens_used", 0), "runden": agent._rounds}
    if final.stop_reason == "refusal":
        del agent.messages[ctx.start:]
        ctx.final_text = "Dabei kann ich leider nicht helfen."
        ctx.on_sentence(ctx.final_text)
        return {"step_count": steps, "last_stop": "refusal", "usage_totals": usage}
    agent.messages.append({"role": "assistant", "content": final.content})
    calls = [b for b in final.content if b.type == "tool_use"]
    stop = final.stop_reason
    if stop == "max_tokens" and calls:
        stop = "max_tokens_tools"
    ctx.calls = calls if stop == "tool_use" else []
    if stop not in ("tool_use", "pause_turn", "max_tokens_tools"):
        ctx.final_text = "".join(ctx.answer).strip()
    return {"step_count": steps, "last_stop": stop, "usage_totals": usage,
            "messages": _bounded_context(agent.messages)}


def run_tools(state: TurnState, runtime: Runtime) -> dict:
    ctx = _ctx(runtime)
    _check(ctx, state)
    agent, calls = ctx.agent, ctx.calls
    ctx.calls = []
    results = agent._run_tools(calls, ctx.on_tool, ctx.on_step)
    agent.messages.append({"role": "user", "content": results})
    refs, pending = [], list(state.get("pending_action_refs") or [])
    for call, res in zip(calls, results):
        text = res.get("content") if isinstance(res.get("content"), str) else ""
        status = "fehler" if res.get("is_error") else "vorlage" if text.startswith("VORLAGE erstellt") else "ok"
        refs.append({"werkzeug": call.name, "status": status, "id": call.id})
        m = _APPROVAL.search(text)
        if m and m.group(1) not in pending:
            pending.append(m.group(1))
    return {"tool_result_refs": refs, "pending_action_refs": pending, "messages": _bounded_context(agent.messages)}


def after_model(state: TurnState) -> str:
    stop = state.get("last_stop")
    if stop in ("tool_use",):
        return "run_tools"
    if stop in ("retry", "pause_turn"):
        return "model_step" if state.get("step_count", 0) < state.get("max_steps", TASK_STEPS) else "step_limit"
    if stop == "max_tokens_tools":
        return "step_limit"
    return "quality_check"


def after_tools(state: TurnState) -> str:
    return "model_step" if state.get("step_count", 0) < state.get("max_steps", TASK_STEPS) else "step_limit"


def step_limit(state: TurnState, runtime: Runtime) -> dict:
    ctx = _ctx(runtime)
    del ctx.agent.messages[ctx.start:]
    ctx.final_text = "Entschuldigung, die Aufgabe konnte ich nicht abschliessen."
    ctx.on_sentence(ctx.final_text)
    return {"status": "failed", "errors": [f"Schrittgrenze {state.get('max_steps')} erreicht"]}


_CLAIM = re.compile(r"\b(habe|hab)\b[^.!?]{0,60}\b(gesendet|verschickt|angelegt|gebucht|verschoben|ausgeführt)\b", re.I)


def quality_check(state: TurnState, runtime: Runtime) -> dict:
    """Deterministische Plausibilitätsprüfung (kein LLM): leere Antwort, unbelegte Erfolgsmeldungen."""
    ctx = _ctx(runtime)
    answer = ctx.final_text if ctx.final_text is not None else "".join(ctx.answer).strip()
    flags = []
    if not answer:
        flags.append("leere_antwort")
    executed = any(r.get("status") == "ok" for r in state.get("tool_result_refs") or [])
    if _CLAIM.search(answer or "") and not executed:
        flags.append("erfolgsmeldung_ohne_ausfuehrung")
    if flags:
        from . import events

        events.log("graph_qualitaet", hinweise=",".join(flags), thread=state.get("thread_id", "")[-40:])
    return {"answer": (answer or "")[:4000], "quality": flags}


def after_quality(state: TurnState) -> str:
    return "prepare_action" if state.get("pending_action_refs") else "persist_business_result"


def prepare_action(state: TurnState, runtime: Runtime) -> dict:
    """Strukturierte Vorschau je offener Vorlage (aus dem Dispatcher, nicht aus Modelltext) + Payload-Hash."""
    from . import approvals

    previews = {}
    for item in approvals.listing(50):
        if item["id"] in (state.get("pending_action_refs") or []) and item["status"] == "WAITING_FOR_APPROVAL":
            raw = approvals._items.get(item["id"]) or {}
            previews[item["id"]] = {"approval_id": item["id"], "aktion": item["vorschau"]["titel"],
                                    "ziel": item["vorschau"]["ziel"], "daten": item["vorschau"]["zeilen"][:12],
                                    "risiko": "aussenwirkung", "laeuft_ab": item["erstellt"] + approvals.TTL,
                                    "payload_hash": payload_hash(item["werkzeug"], raw.get("schluessel", ""))}
    first = next(iter(previews), None)
    return {"pending_action_refs": list(previews), "approval_id": first,
            "action_payload_hash": previews[first]["payload_hash"] if first else None,
            "execution_status": {k: "WAITING_FOR_APPROVAL" for k in previews},
            "status": "waiting" if previews else state.get("status", "running"),
            "action_previews": previews}


def fan_out_approvals(state: TurnState):
    previews = state.get("action_previews") or {}
    if not previews:
        return "persist_business_result"
    return [Send("await_approval", {"preview": p, "thread_id": state.get("thread_id")}) for p in previews.values()]


def await_approval(arg: dict) -> dict:
    """Ein paralleler Zweig je Freigabe: eindeutig per Interrupt-ID adressierbar. Vor interrupt() nur Lesen."""
    preview = arg["preview"]
    decision = interrupt({"typ": "freigabe", **preview})
    return {"decisions": {preview["approval_id"]: decision if isinstance(decision, dict) else {"decision": "?"}}}


def execute_or_reconcile(state: TurnState, runtime: Runtime) -> dict:
    """Führt NICHT aus: liest den tatsächlichen Ausgang beim Dispatcher (einziger Ausführer)."""
    from . import approvals

    status = {}
    for aid in state.get("pending_action_refs") or []:
        item = approvals._items.get(aid)
        status[aid] = item["status"] if item else "UNBEKANNT"
    return {"execution_status": status}


def verify_result(state: TurnState, runtime: Runtime) -> dict:
    status = state.get("execution_status") or {}
    errors = [f"Freigabe {k}: {v}" for k, v in status.items() if v in ("FAILED", "UNCLEAR", "UNBEKANNT")]
    values = list(status.values())
    final = ("completed" if all(v == "COMPLETED" for v in values) else
             "cancelled" if values and all(v == "CANCELLED" for v in values) else
             "waiting" if any(v == "WAITING_FOR_APPROVAL" for v in values) else "partial")
    return {"status": final, "errors": errors}


def persist_business_result(state: TurnState, runtime: Runtime) -> dict:
    from . import events

    status = state.get("status") or "running"
    if status == "running":
        status = "completed"
    events.log("graph_lauf", thread=state.get("thread_id", "")[-40:], run=state.get("run_id"),
               job=state.get("technical_job_id"), status=status, intent=state.get("intent"),
               route=state.get("route"), schritte=state.get("step_count", 0),
               freigaben=len(state.get("pending_action_refs") or []))
    return {"status": status}


def respond(state: TurnState, runtime: Runtime) -> dict:
    return {}


def build() -> StateGraph:
    g = StateGraph(TurnState, context_schema=TurnContext)
    for name, fn in [("validate_request", validate_request), ("load_context", load_context),
                     ("route_intent", route_intent), ("model_step", model_step), ("run_tools", run_tools),
                     ("step_limit", step_limit), ("quality_check", quality_check),
                     ("prepare_action", prepare_action), ("await_approval", await_approval),
                     ("execute_or_reconcile", execute_or_reconcile), ("verify_result", verify_result),
                     ("persist_business_result", persist_business_result), ("respond", respond)]:
        g.add_node(name, fn)
    g.add_edge(START, "validate_request")
    g.add_conditional_edges("validate_request", lambda s: "respond" if s.get("status") == "failed" else "load_context",
                            ["respond", "load_context"])
    g.add_edge("load_context", "route_intent")
    g.add_edge("route_intent", "model_step")
    g.add_conditional_edges("model_step", after_model, ["run_tools", "model_step", "step_limit", "quality_check"])
    g.add_conditional_edges("run_tools", after_tools, ["model_step", "step_limit"])
    g.add_edge("step_limit", "persist_business_result")
    g.add_conditional_edges("quality_check", after_quality, ["prepare_action", "persist_business_result"])
    g.add_conditional_edges("prepare_action", fan_out_approvals, ["await_approval", "persist_business_result"])
    g.add_edge("await_approval", "execute_or_reconcile")
    g.add_edge("execute_or_reconcile", "verify_result")
    g.add_edge("verify_result", "persist_business_result")
    g.add_edge("persist_business_result", "respond")
    g.add_edge("respond", END)
    return g


# --- Service: Checkpointer, Läufe, Resume ---------------------------------------------------------------

class GraphService:
    def __init__(self, path=None):
        self.path = path or (config.data_dir() / CHECKPOINT_FILE)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10.0)
        self.conn.execute("PRAGMA journal_mode=WAL")
        try:
            self.path.chmod(0o600)
        except OSError:
            pass
        self.saver = SqliteSaver(self.conn, serde=JsonPlusSerializer(pickle_fallback=False))
        self.app = build().compile(checkpointer=self.saver)
        self.lock = threading.RLock()

    def _config(self, tid: str, run_id: str | None = None) -> dict:
        cfg = {"configurable": {"thread_id": tid}, "recursion_limit": 3 * TASK_STEPS + 20}
        if run_id:
            cfg["run_id"] = run_id
        return cfg

    def pending(self, tid: str) -> list[dict]:
        snap = self.app.get_state(self._config(tid))
        return [{"interrupt_id": i.id, **(i.value if isinstance(i.value, dict) else {})}
                for task in snap.tasks for i in task.interrupts]

    def _supersede(self, tid: str, ctx: TurnContext) -> None:
        """Offene Freigabe-Zweige ausdrücklich abschließen, bevor eine neue Eingabe den Thread fortsetzt."""
        open_items = self.pending(tid)
        if open_items:
            self.app.invoke(Command(resume={p["interrupt_id"]: {"decision": "ueberholt"} for p in open_items}),
                            self._config(tid), context=ctx, durability="sync")

    def run_turn(self, agent, text: str, on_sentence, on_tool, on_token, on_step) -> tuple[str, list[dict]]:
        from .agent import SentenceSplitter

        scope = agent.memory_store
        tid = thread_id(scope.installation_scope, scope.owner_scope, agent.channel, agent.conversation_id)
        job_id = agent._job["id"]

        def sentence(s: str) -> None:
            from . import telemetry

            telemetry.mark(job_id, "erster_satz_ms")
            on_sentence(s)

        ctx = TurnContext(agent=agent, on_sentence=on_sentence, on_tool=on_tool, on_token=on_token, on_step=on_step,
                          splitter=SentenceSplitter(sentence), answer=agent._partial)
        run_id = str(uuid.uuid4())
        agent._job["graph_run"], agent._job["graph_thread"] = run_id, tid  # Job <-> Graph-Lauf verknuepfen
        with self.lock:
            self._supersede(tid, ctx)
        initial: TurnState = {
            "installation_scope": scope.installation_scope, "owner_scope": scope.owner_scope,
            "conversation_id": agent.conversation_id, "thread_id": tid, "run_id": run_id,
            "technical_job_id": job_id, "business_order_id": None, "channel": agent.channel,
            "generation_id": run_id, "status": "new", "text": text, "plan": [], "task_refs": [],
            "evidence_refs": [], "decision_ref": None, "step_count": 0, "answer": "", "last_stop": "",
            "pending_action_refs": [], "approval_id": None, "action_payload_hash": None,
            "execution_status": {}, "quality": [], "usage_totals": {}, "action_previews": {},
            "budget_limits": {"max_schritte": TASK_STEPS, "zeitlimit_s": RUN_TIMEOUT,
                              "token_budget": int(config.get("job_token_budget", "600000") or 600000)},
            "cancellation_ref": None,
            # Felder mit Reducer je Lauf zuruecksetzen (sonst wuerden sie ueber Zuege im Thread wachsen)
            "decisions": Overwrite({}), "tool_result_refs": Overwrite([]), "errors": Overwrite([]),
        }
        out = self.app.invoke(initial, self._config(tid, run_id), context=ctx, durability="sync")
        answer = ctx.final_text if ctx.final_text is not None else "".join(ctx.answer).strip()
        interrupts = [{"interrupt_id": i.id, **i.value} for i in out.get("__interrupt__", []) or []]
        if interrupts:
            with self.lock:
                _INDEX.update({p["approval_id"]: tid for p in interrupts if p.get("approval_id")})
        return answer, interrupts

    def rebuild_index(self) -> int:
        """Nach einem Neustart: offene Freigabe-Zweige aus den Checkpoints wieder zuordnen."""
        found = 0
        with self.lock:
            for (tid,) in self.conn.execute("SELECT DISTINCT thread_id FROM checkpoints").fetchall():
                for p in self.pending(tid):
                    if p.get("approval_id"):
                        _INDEX[p["approval_id"]] = tid
                        found += 1
        return found

    def resolve(self, approval_id: str, tid: str | None = None) -> bool:
        """Nach Klick/Ja am Dispatcher: wartenden Zweig fortsetzen. Prüft alles; False = nichts fortgesetzt."""
        from . import approvals

        if not tid and approval_id not in _INDEX:
            self.rebuild_index()
        tid = tid or _INDEX.get(approval_id)
        item = approvals._items.get(approval_id)
        if not tid or item is None or item["status"] not in FINAL_APPROVAL:
            return False
        with self.lock:
            match = next((p for p in self.pending(tid) if p.get("approval_id") == approval_id), None)
            if match is None:
                return False
            if match.get("payload_hash") != payload_hash(item["werkzeug"], item.get("schluessel", "")):
                from . import events

                events.log("graph_resume_abgelehnt", grund="payload_geaendert", freigabe=approval_id)
                return False
            dummy = TurnContext(agent=None, on_sentence=lambda s: None, on_tool=lambda n: None,
                                on_token=lambda t: None, on_step=lambda s: None)
            self.app.invoke(Command(resume={match["interrupt_id"]: {"decision": item["status"].lower()}}),
                            self._config(tid), context=dummy, durability="sync")
            if not any(p.get("approval_id") == approval_id for p in self.pending(tid)):
                _INDEX.pop(approval_id, None)
            return True

    def state(self, tid: str) -> dict:
        return dict(self.app.get_state(self._config(tid)).values)

    def prune(self, keep: int = KEEP_CHECKPOINTS, max_age_days: int = MAX_THREAD_AGE_DAYS) -> int:
        """Retention: alte Threads löschen, je Thread nur die neuesten Checkpoints behalten."""
        removed = 0
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=max_age_days)
        with self.lock:
            threads = [r[0] for r in self.conn.execute("SELECT DISTINCT thread_id FROM checkpoints")]
            for tid in threads:
                latest = next(iter(self.saver.list({"configurable": {"thread_id": tid}}, limit=1)), None)
                ts = latest.checkpoint.get("ts") if latest else None
                try:
                    old = ts and datetime.datetime.fromisoformat(ts) < cutoff
                except ValueError:
                    old = False
                if old and not self.pending(tid):
                    self.saver.delete_thread(tid)
                    removed += 1
                    continue
                ids = [r[0] for r in self.conn.execute(
                    "SELECT checkpoint_id FROM checkpoints WHERE thread_id=? ORDER BY checkpoint_id DESC", (tid,))]
                stale = ids[keep:]
                if stale:
                    marks = ",".join("?" * len(stale))
                    self.conn.execute(f"DELETE FROM writes WHERE thread_id=? AND checkpoint_id IN ({marks})", (tid, *stale))
                    self.conn.execute(f"DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_id IN ({marks})",
                                      (tid, *stale))
                    removed += len(stale)
            self.conn.commit()
        return removed


_INDEX: dict[str, str] = {}   # Freigabe-ID -> Thread (Beschleuniger; geprüft wird immer am Checkpoint)
_service: GraphService | None = None
_service_lock = threading.Lock()


def enabled() -> bool:
    return str(config.get("orchestrator", "graph") or "graph").lower() != "legacy"


def service() -> GraphService:
    global _service
    with _service_lock:
        if _service is None or str(_service.path) != str(config.data_dir() / CHECKPOINT_FILE):
            _service = GraphService()
            try:
                _service.prune()
                _service.rebuild_index()
            except Exception:
                pass
            from . import approvals

            approvals.on_resolved(_on_resolved)
        return _service


def _on_resolved(approval_id: str, status: str) -> None:
    if _service is None:
        return
    try:
        _service.resolve(approval_id)
    except Exception as exc:  # Abgleich darf die Freigabe selbst nie scheitern lassen
        from . import events

        events.log("graph_resume_fehler", freigabe=approval_id, fehlerart=type(exc).__name__)


def reset_for_tests() -> None:
    global _service
    with _service_lock:
        if _service is not None:
            try:
                _service.conn.close()
            except Exception:
                pass
        _service = None
        _INDEX.clear()


__all__ = ["GraphService", "TurnState", "build", "classify", "enabled", "payload_hash", "service", "thread_id"]
