/**
 * Messmodell des Command Centers (Etappe 1 des freigegebenen Designs).
 *
 * Zwei Uhren: Server = monotones `t_ms` seit Raumstart, Browser = performance.now(). Der Versatz kommt aus
 * PING/PONG (Messung mit der kleinsten RTT der letzten 20). Jede angezeigte Zahl lässt sich auf genau eine
 * Messstelle zurückführen; was nicht gemessen wurde, bleibt null und wird als „–“ gezeigt.
 * Nichts wird dauerhaft gespeichert: höchstens 50 Züge im Speicher dieser Seite.
 */
import type { AudioMark } from "../audio";
import type { Status } from "../types";

export type ServerPoint =
  | "audio_end_received" | "stt_queued" | "stt_sent" | "stt_start" | "stt_end" | "stt_received" | "stt_rejected" | "stt_failed"
  | "turn_dispatch" | "llm_first_token" | "first_audio_sent" | "generation_done" | "stop";

export type Fragment = {
  id: string;
  vadStart: number | null;
  voicedEnd: number | null;
  cause: "vad" | "manual" | "limit" | null;
  hangoverMs: number;
  aborted: boolean;
  /** Serverzeiten (t_ms, Serveruhr). */
  server: Partial<Record<ServerPoint, number>>;
  failure: { point: "stt_failed" | "stt_rejected"; message: string; t: number } | null;
  consumed: boolean;
  createdAt: number;
};

export type Sentence = { ready?: number; request?: number; firstChunk?: number; failed?: number };

export type Turn = {
  id: string;
  /** realtime = ElevenLabs Agents: Einzelstufen liegen beim Anbieter, keine TRACE-Punkte, nicht auf t = 0 ausgerichtet. */
  source: "voice" | "text" | "realtime";
  /** Nur Echtzeit: Texte des Zugs (im Studio-Modus kommen sie vom Server als TRANSCRIPT). */
  texts?: { role: "user" | "assistant"; text: string }[];
  fragmentIds: string[];
  /** t = 0: Satzende (voiced_end des letzten Fragments) bzw. Absenden bei getippten Nachrichten. Browseruhr. */
  t0: number | null;
  t0Cause: "vad" | "manual" | "limit" | "typed" | null;
  hangoverMs: number;
  createdAt: number;
  server: Partial<Record<ServerPoint, number>>;
  sentences: Record<number, Sentence>;
  audibleStart: number | null;
  audibleEnd: number | null;
  localStop: number | null;
  /** Bis wann bereits geplanter Ton gelaufen wäre (ungesprochener Rest), Browseruhr. */
  unspokenUntil: number | null;
  audibleStop: number | null;
  clockPath: "output_timestamp" | "fallback" | null;
  mutedAtStop: boolean;
  stop: { reason: string; ackMs: number | null } | null;
  /** Beginn des Unterbrechens: vad_start des neuen Einsatzes oder Tastendruck (local_stop). */
  interruptStart: number | null;
  interruptBy: "voice" | "manual" | null;
  error: { stage: string | null; message: string; t: number } | null;
  supersedes: string | null;
  continuedBy: string | null;
  tools: { name: string; t: number }[];
  playbackBlocked: boolean;
};

export type StageKey = "mikro" | "erkennung" | "claude" | "stimme" | "gesamt";
export type UiState = "bereit" | "hoert" | "erkennt" | "denkt" | "spricht" | "fehler" | "offline";
export const UI_STATES: UiState[] = ["bereit", "hoert", "erkennt", "denkt", "spricht", "fehler", "offline"];
/** Vorrang laut Design: Offline > Fehler > Hört zu > Spricht > Denkt nach > Erkennt > Bereit. */
const PRECEDENCE: UiState[] = ["offline", "fehler", "hoert", "spricht", "denkt", "erkennt"];

const MAX_TURNS = 50;
const MAX_FRAGMENTS = 120;
const ERROR_HOLD_MS = 8000;

export type StateFlags = {
  connected: boolean;
  recording: boolean;
  audible: boolean;
  now: number;
};

export type DerivedState = {
  active: UiState;
  /** Gleichzeitig gültige niedrigere Zustände (Nebenmarke). */
  also: UiState[];
  /** Seit wann der aktive Zustand gilt (für laufende ms), Browseruhr. */
  since: number | null;
  error: Turn["error"];
  tool: string | null;
};

const median = (values: number[]) => percentile(values, 50);
export function percentile(values: number[], p: number): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const index = Math.ceil((p / 100) * sorted.length) - 1;
  return sorted[Math.max(0, Math.min(sorted.length - 1, index))];
}
const diff = (end: number | null | undefined, start: number | null | undefined) =>
  end != null && start != null ? end - start : null;

export class Telemetry {
  private readonly fragments = new Map<string, Fragment>();
  turns: Turn[] = [];
  private readonly listeners = new Set<() => void>();
  private revision = 0;
  private clockSamples: { rtt: number; offset: number }[] = [];
  /** server_t − local_t; null bis zum ersten PONG. */
  offset: number | null = null;
  rttMs: number | null = null;
  private typedAt: number | null = null;
  private lastError: Turn["error"] = null;
  private lastActivity = -Infinity;
  private latestServerStatus: Status = "idle";
  disconnectedAt: number | null = null;
  /** Echtzeit-Sitzung (ElevenLabs): null = Studio-Modus. */
  realtime: { mode: "verbindet" | "listening" | "speaking"; toolRunning: boolean } | null = null;
  private realtimeCount = 0;

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  version = () => this.revision;
  private changed() {
    this.revision++;
    this.listeners.forEach((listener) => listener());
  }

  /** Serverzeit → Browserzeit; null ohne Uhrabgleich. */
  toLocal(serverMs: number | null | undefined): number | null {
    if (serverMs == null || this.offset == null) return null;
    return serverMs - this.offset;
  }

  setConnected(connected: boolean, now = performance.now()) {
    const next = connected ? null : (this.disconnectedAt ?? now);
    if (next === this.disconnectedAt) return;
    this.disconnectedAt = next;
    this.changed();
  }

  onPong(sentAt: number, serverT: number, now = performance.now()) {
    if (!Number.isFinite(sentAt) || !Number.isFinite(serverT)) return;
    const rtt = now - sentAt;
    if (rtt < 0 || rtt > 10_000) return;
    this.clockSamples = [...this.clockSamples, { rtt, offset: serverT - (sentAt + rtt / 2) }].slice(-20);
    const best = this.clockSamples.reduce((a, b) => (b.rtt < a.rtt ? b : a));
    this.offset = best.offset;
    this.rttMs = best.rtt;
    this.changed();
  }

  noteTyped(now = performance.now()) {
    this.typedAt = now;
    this.lastActivity = now;
    this.changed();
  }

  private fragment(id: string, now: number): Fragment {
    let item = this.fragments.get(id);
    if (!item) {
      item = { id, vadStart: null, voicedEnd: null, cause: null, hangoverMs: 0, aborted: false, server: {}, failure: null, consumed: false, createdAt: now };
      this.fragments.set(id, item);
      while (this.fragments.size > MAX_FRAGMENTS) this.fragments.delete(this.fragments.keys().next().value!);
    }
    return item;
  }
  turn(id: string | null | undefined): Turn | undefined {
    return id ? this.turns.find((t) => t.id === id) : undefined;
  }
  fragmentsOf(turn: Turn): Fragment[] {
    return turn.fragmentIds.map((id) => this.fragments.get(id)).filter((f): f is Fragment => !!f);
  }

  /** Zug, der gerade hörbar spielt (Ton begonnen, weder zu Ende noch gestoppt). */
  private playingTurn(): Turn | undefined {
    return this.turns.find((t) => t.audibleStart != null && t.audibleEnd == null && t.localStop == null);
  }

  onMark(mark: AudioMark) {
    switch (mark.name) {
      case "vad_start": {
        const f = this.fragment(mark.utterance!, mark.t);
        f.vadStart = mark.t;
        this.lastActivity = mark.t;
        const playing = this.playingTurn();
        if (playing && playing.interruptStart == null) {
          playing.interruptStart = mark.t;
          playing.interruptBy = "voice";
        }
        break;
      }
      case "voiced_end": {
        const f = this.fragment(mark.utterance!, mark.t);
        f.voicedEnd = mark.t;
        f.cause = mark.cause ?? null;
        f.hangoverMs = mark.hangoverMs ?? 0;
        break;
      }
      case "aborted":
        this.fragment(mark.utterance!, mark.t).aborted = true;
        break;
      case "audible_start": {
        const t = this.turn(mark.stream);
        if (t && t.audibleStart == null) {
          t.audibleStart = mark.t;
          t.clockPath = mark.clock ?? null;
        }
        break;
      }
      case "audible_end": {
        const t = this.turn(mark.stream);
        if (t && t.localStop == null) t.audibleEnd = mark.t;
        break;
      }
      case "local_stop": {
        const t = this.turn(mark.stream) ?? this.playingTurn();
        if (t && t.localStop == null && t.audibleEnd == null) {
          t.localStop = mark.t;
          t.unspokenUntil = mark.until ?? null;
          t.mutedAtStop = !!mark.muted;
          if (t.interruptStart == null) {
            t.interruptStart = mark.t;
            t.interruptBy = "manual";
          }
        }
        break;
      }
      case "audible_stop": {
        const t = this.turn(mark.stream);
        if (t && t.localStop != null && t.audibleStop == null) t.audibleStop = mark.t;
        break;
      }
      case "playback_blocked": {
        const t = this.turn(mark.stream);
        if (t && !t.playbackBlocked) {
          t.playbackBlocked = true;
          this.raise({ stage: "audio", message: "Audio blockiert – einmal auf die Seite klicken, damit der Browser Ton erlaubt.", t: mark.t }, t);
        }
        break;
      }
    }
    this.changed();
  }

  private raise(error: NonNullable<Turn["error"]>, turn?: Turn) {
    this.lastError = error;
    if (turn) turn.error = error;
  }

  onServer(message: { type?: string; [key: string]: any }, now = performance.now()) {
    switch (message?.type) {
      case "PONG":
        this.onPong(Number(message.sent_at), Number(message.server_t_ms), now);
        return;
      case "WELCOME":
        if (typeof message.snapshot?.status === "string") this.latestServerStatus = message.snapshot.status as Status;
        break;
      case "STATE":
        if (typeof message.status === "string") this.latestServerStatus = message.status as Status;
        if (message.status === "idle") {
          // Leere oder stille Aufnahme ohne Zug: beendete Fragmente gelten als verbraucht.
          for (const f of this.fragments.values())
            if (this.fragmentClosed(f) || (f.voicedEnd != null && f.server.audio_end_received == null)) f.consumed = true;
        }
        break;
      case "TURN_START": {
        const id = String(message.stream_id ?? "");
        if (!id || this.turn(id)) break;
        const ids: string[] = Array.isArray(message.utterance_ids) ? message.utterance_ids.map(String) : [];
        const source = message.source === "voice" || ids.length ? "voice" : "text";
        const frags = ids.map((u) => this.fragment(u, now));
        frags.forEach((f) => (f.consumed = true));
        const last = frags[frags.length - 1];
        const typed = source === "text" ? this.typedAt : null;
        const turn: Turn = {
          id, source, fragmentIds: ids, createdAt: now,
          t0: source === "voice" ? (last?.voicedEnd ?? null) : typed,
          t0Cause: source === "voice" ? (last?.cause ?? null) : typed != null ? "typed" : null,
          hangoverMs: source === "voice" ? (last?.hangoverMs ?? 0) : 0,
          server: {}, sentences: {}, audibleStart: null, audibleEnd: null, localStop: null, unspokenUntil: null, audibleStop: null, clockPath: null,
          mutedAtStop: false, stop: null, interruptStart: null, interruptBy: null, error: null,
          supersedes: typeof message.supersedes === "string" ? message.supersedes : null, continuedBy: null, tools: [], playbackBlocked: false,
        };
        if (source === "text") this.typedAt = null;
        const previous = this.turn(turn.supersedes);
        if (previous && previous.stop?.reason === "continuation") previous.continuedBy = id;
        this.turns = [turn, ...this.turns].slice(0, MAX_TURNS);
        break;
      }
      case "TRACE": {
        const point = String(message.point ?? "") as ServerPoint | "sentence_ready" | "tts_request" | "tts_first_chunk" | "tts_failed";
        const t = Number(message.t_ms);
        if (!Number.isFinite(t)) break;
        if (message.utterance_id && !message.stream_id) {
          const f = this.fragment(String(message.utterance_id), now);
          if (point === "stt_failed" || point === "stt_rejected") f.failure = { point, message: String(message.message ?? ""), t };
          else f.server[point as ServerPoint] = t;
          break;
        }
        const turn = this.turn(message.stream_id);
        if (!turn) break;
        const n = Number(message.n);
        if (point === "sentence_ready" || point === "tts_request" || point === "tts_first_chunk" || point === "tts_failed") {
          if (!Number.isInteger(n) || n < 1) break;
          const s = (turn.sentences[n] ??= {});
          if (point === "sentence_ready") s.ready = t;
          else if (point === "tts_request") s.request = t;
          else if (point === "tts_first_chunk") s.firstChunk = t;
          else s.failed = t;
          const all = Object.values(turn.sentences);
          if (point === "tts_failed" && turn.server.generation_done != null && all.length && all.every((x) => x.failed != null))
            this.raise({ stage: "tts", message: "Stimme ausgefallen: kein Satz konnte vertont werden.", t: now }, turn);
          break;
        }
        if (turn.server[point as ServerPoint] == null) turn.server[point as ServerPoint] = t;
        if (point === "generation_done") {
          const all = Object.values(turn.sentences);
          if (all.length && all.every((x) => x.failed != null))
            this.raise({ stage: "tts", message: "Stimme ausgefallen: kein Satz konnte vertont werden.", t: now }, turn);
        }
        break;
      }
      case "STREAM_STOPPED": {
        const turn = this.turn(message.stream_id);
        if (turn && !turn.stop)
          turn.stop = { reason: String(message.reason ?? ""), ackMs: typeof message.ack_ms === "number" ? message.ack_ms : null };
        break;
      }
      case "TOOL": {
        const turn = this.turn(message.stream_id);
        if (turn && typeof message.name === "string") turn.tools.push({ name: message.name, t: now });
        break;
      }
      case "ERROR": {
        const error = { stage: typeof message.stage === "string" ? message.stage : null, message: String(message.message ?? "Fehler"), t: now };
        this.raise(error, this.turn(message.stream_id));
        if (typeof message.utterance_id === "string") this.fragment(message.utterance_id, now).consumed = true;
        break;
      }
      default:
        return;
    }
    this.changed();
  }

  private fragmentOpen(f: Fragment) {
    return f.server.stt_queued != null && f.server.stt_received == null && f.server.stt_end == null && !f.failure;
  }
  private fragmentClosed(f: Fragment) {
    return f.aborted || !!f.failure || f.server.stt_received != null || f.server.stt_end != null;
  }

  // --- Echtzeit-Modus: Ereignisse des ElevenLabs-Clients ---
  private realtimeTurn(): Turn | undefined {
    return this.turns.find((t) => t.source === "realtime");
  }
  realtimeState(state: Telemetry["realtime"]) {
    this.realtime = state;
    this.changed();
  }
  realtimeUser(text: string, now = performance.now()) {
    const turn: Turn = {
      id: `rt-${++this.realtimeCount}`, source: "realtime", texts: [{ role: "user", text }], fragmentIds: [], t0: null, t0Cause: null,
      hangoverMs: 0, createdAt: now, server: {}, sentences: {}, audibleStart: null, audibleEnd: null, localStop: null, unspokenUntil: null,
      audibleStop: null, clockPath: null, mutedAtStop: false, stop: null, interruptStart: null, interruptBy: null, error: null,
      supersedes: null, continuedBy: null, tools: [], playbackBlocked: false,
    };
    this.lastActivity = now;
    this.turns = [turn, ...this.turns].slice(0, MAX_TURNS);
    this.changed();
  }
  realtimeAgent(text: string) {
    const turn = this.realtimeTurn();
    if (turn) turn.texts = [...(turn.texts ?? []), { role: "assistant", text }];
    this.changed();
  }
  realtimeMode(mode: "listening" | "speaking", now = performance.now()) {
    const turn = this.realtimeTurn();
    if (turn && mode === "speaking" && turn.audibleStart == null) turn.audibleStart = now;
    if (turn && mode === "listening" && turn.audibleStart != null && turn.audibleEnd == null && turn.localStop == null) turn.audibleEnd = now;
    if (this.realtime) this.realtime = { ...this.realtime, mode };
    this.changed();
  }
  realtimeTool(name: string, running: boolean, now = performance.now()) {
    const turn = this.realtimeTurn();
    if (turn && running) turn.tools.push({ name, t: now });
    if (this.realtime) this.realtime = { ...this.realtime, toolRunning: running };
    this.changed();
  }
  realtimeInterrupted(now = performance.now()) {
    const turn = this.realtimeTurn();
    if (turn && turn.localStop == null && turn.audibleEnd == null) {
      turn.localStop = now;
      turn.interruptBy = "voice";
    }
    this.changed();
  }
  realtimeError(message: string, now = performance.now()) {
    this.raise({ stage: "realtime", message, t: now }, this.realtimeTurn());
    this.changed();
  }

  /** Zustand mit Vorrang; Rückfall auf den letzten Server-STATE, nie „Bereit“ mitten im Zug. */
  derive(flags: StateFlags): DerivedState {
    const { now } = flags;
    const truth = new Map<UiState, number | null>();
    if (!flags.connected) truth.set("offline", this.disconnectedAt);
    const err = this.lastError;
    if (err && now - err.t < ERROR_HOLD_MS && !(this.lastActivity > err.t)) truth.set("fehler", err.t);
    if (this.realtime) {
      // Echtzeit: Zustände kommen vom ElevenLabs-Client (Mikro offen = hört zu).
      const rt = this.realtime;
      const turn = this.realtimeTurn();
      if (rt.mode === "speaking") truth.set("spricht", turn?.audibleStart ?? null);
      if (rt.toolRunning || rt.mode === "verbindet") truth.set("denkt", turn?.tools.at(-1)?.t ?? null);
      if (rt.mode === "listening" && !rt.toolRunning) truth.set("hoert", null);
      const active = PRECEDENCE.find((s) => truth.has(s)) ?? "bereit";
      return { active, also: PRECEDENCE.filter((s) => s !== active && truth.has(s) && s !== "offline"), since: truth.get(active) ?? null,
        error: truth.has("fehler") ? err : null, tool: rt.toolRunning ? (turn?.tools.at(-1)?.name ?? null) : null };
    }
    const live = [...this.fragments.values()].filter((f) => !f.consumed && !f.aborted && now - f.createdAt < 120_000);
    const recordingFrag = live.find((f) => f.vadStart != null && f.server.audio_end_received == null && !this.fragmentClosed(f));
    const server = this.latestServerStatus;
    if (flags.recording || server === "listening" || recordingFrag)
      truth.set("hoert", recordingFrag?.vadStart ?? null);
    const current = this.turns[0];
    if (flags.audible) truth.set("spricht", current?.audibleStart ?? null);
    const thinkingTurn = current && current.audibleStart == null && current.stop == null && !current.playbackBlocked && !current.error
      && !(current.server.generation_done != null && current.server.first_audio_sent == null) ? current : undefined;
    if (thinkingTurn || server === "thinking" || (server === "speaking" && !flags.audible && !current?.localStop))
      truth.set("denkt", thinkingTurn?.t0 ?? this.toLocal(thinkingTurn?.server.turn_dispatch) ?? null);
    const open = live.filter((f) => this.fragmentOpen(f) || (f.server.stt_received != null && !f.failure));
    if (open.length || server === "transcribing")
      truth.set("erkennt", open.length ? this.toLocal(Math.min(...open.map((f) => f.server.stt_queued ?? Infinity))) : null);
    const active = PRECEDENCE.find((s) => truth.has(s)) ?? "bereit";
    const also = PRECEDENCE.filter((s) => s !== active && truth.has(s) && s !== "offline");
    const tool = thinkingTurn?.tools.at(-1)?.name ?? null;
    return { active, also, since: truth.get(active) ?? null, error: truth.has("fehler") ? err : null, tool };
  }

  /** Kachelwerte eines Zugs (ms) – jede Zahl genau eine Messstelle. */
  stages(turn: Turn): Record<StageKey, number | null> {
    if (turn.source === "realtime") return { mikro: null, erkennung: null, claude: null, stimme: null, gesamt: null };
    const frags = this.fragmentsOf(turn);
    const first = frags[0];
    const last = frags[frags.length - 1];
    const s1 = turn.sentences[1];
    return {
      mikro: diff(last?.voicedEnd, first?.vadStart),
      erkennung: diff(last?.server.stt_end, last?.server.stt_start),
      claude: diff(turn.server.llm_first_token, turn.server.turn_dispatch),
      stimme: diff(s1?.firstChunk, s1?.request),
      gesamt: diff(turn.audibleStart, turn.t0),
    };
  }

  /** Zerlegung von Gesamt (nur Abschnitte nach t = 0); Rest = Übergaben/Netz, negativ = Überlappung. */
  breakdown(turn: Turn): { label: string; ms: number | null }[] {
    const frags = this.fragmentsOf(turn);
    const last = frags[frags.length - 1];
    const s1 = turn.sentences[1];
    const parts: { label: string; ms: number | null }[] = [];
    if (turn.t0Cause === "vad") parts.push({ label: "Nachlauf", ms: turn.hangoverMs });
    if (turn.source === "voice") {
      parts.push({ label: "Warteschlange", ms: diff(last?.server.stt_start, last?.server.stt_queued) });
      parts.push({ label: "Erkennung", ms: diff(last?.server.stt_end, last?.server.stt_start) });
      parts.push({ label: "Sammeln", ms: diff(turn.server.turn_dispatch, last?.server.stt_end) });
    }
    parts.push({ label: "Claude", ms: diff(turn.server.llm_first_token, turn.server.turn_dispatch) });
    parts.push({ label: "Erster Satz", ms: diff(s1?.ready, turn.server.llm_first_token) });
    parts.push({ label: "Stimme", ms: diff(s1?.firstChunk, s1?.request) });
    parts.push({ label: "Auslieferung", ms: diff(turn.audibleStart, this.toLocal(turn.server.first_audio_sent)) });
    const total = this.stages(turn).gesamt;
    if (total != null) {
      const known = parts.reduce((sum, p) => sum + (p.ms ?? 0), 0);
      const rest = total - known;
      parts.push({ label: rest < 0 ? "Überlappung" : "Übergaben/Netz", ms: rest });
    }
    return parts;
  }

  /** Gefühlte Stoppzeit: Beginn des Unterbrechens bis gemessene Stille am Ausgang. Stumm: nicht hörbar → null. */
  stopAfter(turn: Turn): number | null {
    if (turn.mutedAtStop) return null;
    return diff(turn.audibleStop, turn.interruptStart);
  }

  sessionStats(stage: StageKey): { median: number | null; p95: number | null; count: number } {
    const values = this.turns.map((t) => this.stages(t)[stage]).filter((v): v is number => v != null && v >= 0);
    return { median: median(values), p95: percentile(values, 95), count: values.length };
  }

  bargeStats(): { count: number; median: number | null } {
    const values = this.turns.map((t) => this.stopAfter(t)).filter((v): v is number => v != null && v >= 0);
    return { count: this.turns.filter((t) => t.localStop != null).length, median: median(values) };
  }
}
