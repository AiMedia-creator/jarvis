import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { AudioEngine } from "../audio";
import type { Role, Transcript } from "../types";
import type { Approval, Automations, Capability, CapabilityRun, Settings } from "./api";
import { CapabilityPanel } from "./CapabilityPanel";
import type { RealtimeSession } from "./realtime";
import { SettingsDialog, type VoicePrefs } from "./SettingsDialog";
import { MiniLevel, SignalBand, type BandMeter } from "./SignalBand";
import { fmtMs, TurnTape } from "./TurnTape";
import { type StageKey, type Telemetry, type UiState, UI_STATES } from "./telemetry";
import "./center.css";

const LABEL: Record<UiState, string> = {
  bereit: "Bereit", hoert: "Hört zu", erkennt: "Erkennt", denkt: "Denkt nach", spricht: "Spricht", fehler: "Fehler", offline: "Offline",
};
const STAGES: { key: StageKey; label: string; sub: string }[] = [
  { key: "mikro", label: "Mikro", sub: "Sprechdauer" },
  { key: "erkennung", label: "Erkennung", sub: "Whisper dekodiert" },
  { key: "claude", label: "Claude", sub: "bis erstes Token" },
  { key: "stimme", label: "Stimme", sub: "bis erster Ton-Chunk" },
  { key: "gesamt", label: "Gesamt", sub: "Satzende → erster Ton" },
];
const STATE_STAGE: Partial<Record<UiState, StageKey>> = { hoert: "mikro", erkennt: "erkennung", denkt: "claude", spricht: "stimme" };
const STAGE_NAMES: Record<string, string> = { stt: "Erkennung", llm: "Claude", tts: "Stimme", audio: "Audio", connection: "Verbindung", realtime: "Echtzeit" };

function Glyph({ state }: { state: UiState }) {
  const common = { width: 14, height: 14, viewBox: "0 0 14 14", "aria-hidden": true, fill: "none", stroke: "currentColor", strokeWidth: 1.6 } as const;
  switch (state) {
    case "bereit": return <svg {...common}><circle cx="7" cy="7" r="5" /></svg>;
    case "hoert": return <svg {...common}><circle cx="7" cy="7" r="5" fill="currentColor" /></svg>;
    case "erkennt": return <svg {...common} stroke="none" fill="currentColor"><circle cx="2.5" cy="7" r="1.6" /><circle cx="7" cy="7" r="1.6" /><circle cx="11.5" cy="7" r="1.6" /></svg>;
    case "denkt": return <svg {...common}><path d="M3 1.5h8M3 12.5h8M4 1.5c0 3 6 3 6 5.5S4 9.5 4 12.5M10 1.5c0 3-6 3-6 5.5s6 2.5 6 5.5" /></svg>;
    case "spricht": return <svg {...common}><path d="M2 5h2.5L8 2v10L4.5 9H2z" fill="currentColor" /><path d="M10 4.5c1 1.4 1 3.6 0 5M11.8 3c1.8 2.3 1.8 5.7 0 8" /></svg>;
    case "fehler": return <svg {...common}><path d="M7 1.5 13 12.5H1z" /><path d="M7 5.5v3M7 10.2v.3" /></svg>;
    case "offline": return <svg {...common}><circle cx="7" cy="7" r="5" /><path d="M3 11 11 3" /></svg>;
  }
}

function useReducedMotion(): boolean {
  const query = typeof window !== "undefined" && window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  const [reduced, setReduced] = useState(query?.matches ?? false);
  useEffect(() => {
    if (!query) return;
    const on = () => setReduced(query.matches);
    query.addEventListener?.("change", on);
    return () => query.removeEventListener?.("change", on);
  }, [query]);
  return reduced;
}

/** Uhr für laufende Werte: 10-ms-Schritte während ein Zustand läuft, sonst 4 Hz (auch bei reduzierter Bewegung). */
function useNow(running: boolean, reduced: boolean): number {
  const [now, setNow] = useState(() => performance.now());
  useEffect(() => {
    if (!running || reduced) {
      const timer = window.setInterval(() => setNow(performance.now()), 250);
      return () => window.clearInterval(timer);
    }
    let frame = 0;
    let last = 0;
    const loop = (t: number) => {
      if (t - last >= 10) {
        last = t;
        setNow(performance.now());
      }
      frame = requestAnimationFrame(loop);
    };
    frame = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(frame);
  }, [running, reduced]);
  return now;
}

const isTyping = (target: EventTarget | null) => {
  const el = target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
};

function micErrorText(error: unknown): string {
  const name = (error as { name?: string })?.name ?? "";
  if (name === "NotAllowedError" || name === "SecurityError")
    return "Mikrofon ist blockiert. Erlaube den Zugriff über das Schloss-Symbol in der Adressleiste und versuch es erneut.";
  if (name === "NotFoundError" || name === "OverconstrainedError") return "Kein Mikrofon gefunden. Schließ eins an oder wähle es in den Systemeinstellungen.";
  if (name === "NotReadableError" || name === "AbortError") return "Das Mikrofon wird gerade von einer anderen App benutzt oder ist nicht lesbar.";
  return "Mikrofon konnte nicht gestartet werden (AudioWorklet nicht verfügbar?). Seite neu laden und erneut versuchen.";
}

export type CommandCenterProps = {
  telemetry: Telemetry;
  audio: AudioEngine | null;
  role: Role;
  connected: boolean;
  transcripts: Transcript[];
  micOn: boolean;
  onMicChange: (on: boolean) => void;
  muted: boolean;
  onMutedChange: (muted: boolean) => void;
  onAsk: (text: string) => void;
  onStop: () => void;
  onLog: (message: string, level?: "info" | "warn") => void;
  automations: Automations | null;
  approvals: Approval[];
  onOpenApprovals: () => void;
  settings?: Settings | null;
  onSettings?: (settings: Settings) => void;
  voiceMode?: "studio" | "echtzeit";
  onStartRealtime?: () => Promise<void>;
  onStopRealtime?: () => Promise<void>;
  getRealtime?: () => RealtimeSession | null;
  prefs?: VoicePrefs;
  onPrefs?: (prefs: VoicePrefs) => void;
  onReset?: () => void;
  capabilities?: Capability[] | null;
  capabilityRuns?: CapabilityRun[];
  capabilityError?: string | null;
  capabilityBusy?: string | null;
  onCapabilityAction?: (id: string, action: "abbrechen" | "wiederholen") => void;
  onOpenOrder?: (id: string) => void;
};

/** Command Center: Regieplatz mit echter Messung der Sprachkette (ersetzt den Bereich „Gespräch“). */
export function CommandCenter({
  telemetry, audio, role, connected, transcripts, micOn, onMicChange, muted, onMutedChange, onAsk, onStop, onLog,
  automations, approvals, onOpenApprovals, settings = null, onSettings = () => undefined, voiceMode = "studio",
  onStartRealtime = async () => undefined, onStopRealtime = async () => undefined, getRealtime = () => null,
  prefs = { pause: 1.2, barge: "frei", talkMode: "frei" }, onPrefs = () => undefined, onReset = () => undefined,
  capabilities = null, capabilityRuns = [], capabilityError = null, capabilityBusy = null,
  onCapabilityAction = () => undefined, onOpenOrder = () => undefined,
}: CommandCenterProps) {
  const toolLabel = (name: string) => capabilities?.find((c) => c.name === name)?.titel ?? name;
  const realtimeMode = voiceMode === "echtzeit";
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [switching, setSwitching] = useState(false);
  const ptt = !realtimeMode && prefs.talkMode === "ptt";
  const version = useSyncExternalStore(telemetry.subscribe, telemetry.version, telemetry.version);
  const reduced = useReducedMotion();
  const owner = role === "owner";
  const [text, setText] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [permission, setPermission] = useState<PermissionState | "unbekannt">("unbekannt");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [details, setDetails] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const [fast, setFast] = useState(false);

  // Engine-Flags (Aufnahme, hörbare Wiedergabe) werden bei jedem Takt neu gelesen.
  const now = useNow(fast, reduced);
  const derived = telemetry.derive({
    connected, recording: audio?.isRecording() ?? false, audible: audio?.isAudible() ?? false, now,
  });
  const running = derived.active !== "bereit" && derived.active !== "offline";
  useEffect(() => setFast(running), [running]);

  useEffect(() => {
    let status: PermissionStatus | null = null;
    navigator.permissions?.query({ name: "microphone" as PermissionName })
      .then((result) => {
        status = result;
        setPermission(result.state);
        result.onchange = () => setPermission(result.state);
      })
      .catch(() => setPermission("unbekannt"));
    return () => { if (status) status.onchange = null; };
  }, []);

  const turns = telemetry.turns;
  const selected = (selectedId && telemetry.turn(selectedId)) || turns[0] || null;
  useEffect(() => {
    if (selectedId && !telemetry.turn(selectedId)) setSelectedId(null);
  }, [version, selectedId, telemetry]);

  const toggleMic = useCallback(async () => {
    setNotice(null);
    if (realtimeMode) {
      // Im Echtzeit-Modus gehört das Mikrofon der ElevenLabs-Sitzung: der Knopf beendet sie.
      setSwitching(true);
      try { await onStopRealtime(); } finally { setSwitching(false); }
      return;
    }
    if (!owner) return setNotice("Nur das steuernde Fenster darf das Mikrofon nutzen – dieses Fenster ist eine Ansicht.");
    if (!audio) return setNotice("Der Audiokanal ist noch nicht verbunden. Einen Moment – oder Jarvis neu starten, falls das bleibt.");
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia)
      return setNotice("Das Mikrofon funktioniert nur über http://localhost oder https – öffne Jarvis unter localhost.");
    try {
      if (!micOn) {
        await audio.enableMic();
        onMicChange(true);
        setPermission("granted");
      } else {
        audio.disableMic();
        onMicChange(false);
      }
    } catch (error) {
      const message = micErrorText(error);
      setNotice(message);
      onLog(message, "warn");
    }
  }, [audio, micOn, onLog, onMicChange, onStopRealtime, owner, realtimeMode]);

  const switchMode = useCallback(async (next: "studio" | "echtzeit") => {
    if (next === voiceMode || switching) return;
    setNotice(null);
    setSwitching(true);
    try {
      if (next === "echtzeit") await onStartRealtime();
      else await onStopRealtime();
    } finally {
      setSwitching(false);
    }
  }, [onStartRealtime, onStopRealtime, switching, voiceMode]);

  // Push-to-talk: Mikro-Knopf oder Leertaste gedrückt halten (Mikro wird beim ersten Drücken eingeschaltet).
  const pttDown = useCallback(async () => {
    if (!ptt || !owner || !audio) return;
    if (!micOn) {
      await toggleMic();
      return;
    }
    audio.pttDown();
  }, [audio, micOn, owner, ptt, toggleMic]);
  const pttUp = useCallback(() => { if (ptt) audio?.pttUp(); }, [audio, ptt]);

  const send = useCallback(() => {
    const value = text.trim();
    if (!value || !owner) return;
    onAsk(value);
    setText("");
  }, [onAsk, owner, text]);

  const canCancel = derived.active === "hoert" || derived.active === "spricht" || derived.active === "denkt"
    || derived.also.includes("spricht") || derived.also.includes("denkt");

  // Tastatur: Buchstaben/Pfeile nur außerhalb von Textfeldern; Esc regelt die App (Aufnahme > Sprechen > Denken).
  useEffect(() => {
    if (!ptt) return;
    const down = (event: KeyboardEvent) => {
      if (event.code !== "Space" || event.repeat || isTyping(event.target) || settingsOpen) return;
      const tag = (event.target as HTMLElement | null)?.tagName;
      if (tag === "BUTTON" || tag === "A") return;
      event.preventDefault();
      void pttDown();
    };
    const up = (event: KeyboardEvent) => { if (event.code === "Space") pttUp(); };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, [ptt, pttDown, pttUp, settingsOpen]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || isTyping(event.target) || settingsOpen) return;
      const key = event.key;
      if ((key === "m" || key === "M") && !ptt) { event.preventDefault(); void toggleMic(); }
      else if (key === "s" || key === "S") { event.preventDefault(); onMutedChange(!muted); }
      else if (key === "/") { event.preventDefault(); inputRef.current?.focus(); }
      else if (key === "ArrowUp" || key === "ArrowDown") {
        if (!turns.length) return;
        event.preventDefault();
        const index = Math.max(0, turns.findIndex((t) => t.id === selected?.id));
        const next = key === "ArrowUp" ? Math.max(0, index - 1) : Math.min(turns.length - 1, index + 1);
        setSelectedId(turns[next].id);
        document.getElementById(`turn-${turns[next].id}`)?.scrollIntoView({ block: "nearest" });
      } else if (key === "Enter") {
        const tag = (event.target as HTMLElement | null)?.tagName;
        if (tag === "BUTTON" || tag === "A") return;
        if (turns.length) { event.preventDefault(); setDetails((d) => !d); }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [muted, onMutedChange, ptt, selected?.id, settingsOpen, toggleMic, turns]);

  const jarvis = automations?.agenten.find((a) => a.id === "jarvis");
  const speech = automations?.sprache ?? null;
  const stagesNow = selected ? telemetry.stages(selected) : null;
  const activeStage = STATE_STAGE[derived.active] ?? null;
  const barge = telemetry.bargeStats();
  const selectedTexts = useMemo(() => {
    if (selected?.source === "realtime")
      return (selected.texts ?? []).map((t, i) => ({ id: `${selected.id}-${i}`, role: t.role, text: t.text, stream_id: selected.id, final: true }));
    if (!selected) return transcripts.slice(-4);
    const own = transcripts.filter((t) => t.stream_id === selected.id);
    return own.length ? own : [];
  }, [selected, transcripts]);
  const openApprovals = approvals.filter((a) => a.status === "WAITING_FOR_APPROVAL");
  const running_ms = derived.since != null ? Math.max(0, now - derived.since) : null;
  const errorText = derived.error ? `${derived.error.stage ? `${STAGE_NAMES[derived.error.stage] ?? derived.error.stage}: ` : ""}${derived.error.message}` : null;

  const bandSource: (() => BandMeter | null) | null = realtimeMode
    ? () => {
        const session = getRealtime();
        if (!session) return null;
        const i = Math.max(0, Math.min(1, session.inputVolume())), o = Math.max(0, Math.min(1, session.outputVolume()));
        return { input: { peak: -60 + 60 * i, rms: -60 + 60 * i }, output: { peak: -60 + 60 * o, rms: -60 + 60 * o }, muted, relative: { input: i, output: o } };
      }
    : audio ? () => audio.meter() : null;

  const segmentContent = (state: UiState) => {
    if (state !== derived.active) return derived.also.includes(state) ? <i className="cc-side-dot" title="gilt gleichzeitig" /> : null;
    switch (state) {
      case "hoert": return <MiniLevel audio={audio} kind="input" reduced={reduced} level={realtimeMode ? () => getRealtime()?.inputVolume() ?? 0 : null} />;
      case "erkennt":
      case "denkt":
        return <span className="mono cc-run">{running_ms != null ? fmtMs(Math.round(running_ms / 10) * 10) : ""}{state === "denkt" && derived.tool ? ` · ${toolLabel(derived.tool)}` : ""}</span>;
      case "spricht": return muted ? <span className="cc-run">stumm</span> : <MiniLevel audio={audio} kind="output" reduced={reduced} level={realtimeMode ? () => getRealtime()?.outputVolume() ?? 0 : null} />;
      default: return null;
    }
  };

  return (
    <div className={`cc-center ${derived.active === "offline" ? "is-offline" : ""}`} data-state={derived.active}>
      <header className="cc-hdr-facts" aria-label="Was Jarvis gerade nutzt">
        <span className={`cc-fact ${connected ? "ok" : "warn"}`}><b>Verbindung</b>{connected ? `verbunden${telemetry.rttMs != null ? ` · RTT ${fmtMs(telemetry.rttMs)}` : ""}` : "getrennt"}</span>
        {realtimeMode ? (
          <>
            <span className="cc-fact"><b>Sprachkern</b>ElevenLabs Agents{settings?.eleven_tts_model ? ` · ${settings.eleven_tts_model}` : ""}</span>
            <span className="cc-fact"><b>Gehirn</b>{settings?.modell ? `${settings.modell} (beim Anbieter)` : "–"}</span>
            <span className="cc-fact"><b>Erkennung</b>beim Anbieter</span>
          </>
        ) : (
          <>
            <span className="cc-fact"><b>Gehirn</b>{jarvis ? `${jarvis.modell}${jarvis.status !== "bereit" ? ` · ${jarvis.hinweis || "nicht eingerichtet"}` : ""}` : "–"}</span>
            <span className="cc-fact" title={speech?.stimme_hinweis || undefined}><b>Stimme</b>{speech ? `${speech.stimme} · ${speech.stimme_anbieter}${speech.stimme_modell ? ` (${speech.stimme_modell})` : ""}` : "–"}</span>
            <span className="cc-fact"><b>Erkennung</b>{speech?.erkennung ?? "–"}</span>
          </>
        )}
        <span className={`cc-fact ${permission === "denied" ? "warn" : ""}`}><b>Mikrofon</b>{realtimeMode ? "ElevenLabs-Sitzung" : micOn ? (ptt ? "an · Push-to-talk" : "an") : "aus"}{permission === "denied" ? " · blockiert" : permission === "prompt" ? " · noch nicht erlaubt" : ""}</span>
        <div className="cc-mode" role="radiogroup" aria-label="Sprachmodus">
          {(["studio", "echtzeit"] as const).map((m) => {
            const blockedByTurn = m === "echtzeit" && !realtimeMode && derived.active !== "bereit" && derived.active !== "fehler";
            const unavailable = m === "echtzeit" && settings != null && !settings.sprachkern_verfuegbar;
            const reason = !owner ? "Nur im steuernden Fenster" : unavailable ? (settings?.sprachkern_problem || "ElevenLabs ist nicht eingerichtet")
              : blockedByTurn ? "Erst Esc oder Zugende abwarten" : undefined;
            return (
              <button key={m} type="button" role="radio" aria-checked={voiceMode === m} className={voiceMode === m ? "on" : ""}
                disabled={!owner || switching || (voiceMode !== m && (unavailable || blockedByTurn))} title={reason}
                onClick={() => void switchMode(m)}>{m === "studio" ? "Studio" : "Echtzeit"}</button>
            );
          })}
        </div>
      </header>

      <ol className="cc-states" aria-label="Zustand">
        {UI_STATES.map((state) => (
          <li key={state} className={`cc-seg ${state} ${state === derived.active ? "active" : ""}`} aria-current={state === derived.active ? "step" : undefined}>
            <Glyph state={state} />
            <span>{LABEL[state]}</span>
            {segmentContent(state)}
          </li>
        ))}
      </ol>
      <p className="cc-sr" role="status" aria-live="polite">Zustand: {LABEL[derived.active]}</p>
      {errorText && <p className="cc-alert" role="alert">{errorText}</p>}
      {notice && <p className="cc-alert soft" role="alert">{notice} <button type="button" className="cc-link" onClick={() => setNotice(null)}>OK</button></p>}

      <div className="cc-center-grid">
        <div className="cc-center-main">
          {realtimeMode || selected?.source === "realtime" ? (
            <ol className="cc-chain realtime" aria-label="Regie-Kette (Echtzeit)">
              <li className={`cc-tile agent ${derived.active !== "bereit" ? "active" : ""}`}>
                <span className="cc-tile-label">ElevenLabs Agents</span>
                <strong className="mono">–</strong>
                <span className="cc-tile-sub">Erkennung, Claude und Stimme laufen beim Anbieter; er meldet keine Einzelzeiten.</span>
                <span className="cc-tile-stats">Werkzeuge im Zug: {selected?.tools.length ?? 0}</span>
              </li>
              <li className="cc-tile gesamt">
                <span className="cc-tile-label">Gesamt</span>
                <strong className="mono">–</strong>
                <span className="cc-tile-sub">kein Satzende vom Anbieter</span>
              </li>
            </ol>
          ) : (
          <ol className="cc-chain" aria-label="Regie-Kette dieses Zugs">
            {STAGES.map((stage) => {
              const stats = telemetry.sessionStats(stage.key);
              const value = stagesNow?.[stage.key] ?? null;
              return (
                <li key={stage.key} className={`cc-tile ${stage.key} ${activeStage === stage.key ? "active" : ""}`}>
                  <span className="cc-tile-label">{stage.label}</span>
                  <strong className="mono" aria-label={`${stage.label}: ${fmtMs(value)}`}>{fmtMs(value)}</strong>
                  <span className="cc-tile-sub">{stage.sub}</span>
                  <span className="cc-tile-stats mono">Median {fmtMs(stats.median)} · P95 {fmtMs(stats.p95)}</span>
                </li>
              );
            })}
          </ol>
          )}

          <CapabilityPanel capabilities={capabilities} runs={capabilityRuns} error={capabilityError} canEdit={owner}
            busy={capabilityBusy} onAction={onCapabilityAction} onOpenOrder={onOpenOrder} />

          <SignalBand source={bandSource} connected={connected} disconnectedAt={telemetry.disconnectedAt} reduced={reduced} redrawKey={version} />

          <TurnTape telemetry={telemetry} selectedId={selected?.id ?? null} onSelect={setSelectedId}
            details={details} onToggleDetails={() => setDetails((d) => !d)} now={now} />
        </div>

        <aside className="cc-center-side" aria-label="Gesprächszug im Detail">
          <section className="cc-transcript" aria-live="polite">
            <h3>{selected ? "Transkript dieses Zugs" : "Transkript"}</h3>
            {selectedTexts.length === 0 && <p className="cc-empty">Noch kein Text – sprich oder schreib Jarvis.</p>}
            {selectedTexts.map((t) => (
              <p key={t.id} className={`cc-line ${t.role} ${t.final ? "" : "pending"}`}><b>{t.role === "user" ? "Du" : "Jarvis"}</b>{t.text}</p>
            ))}
          </section>
          <section>
            <h3>Werkzeuge im Zug</h3>
            {selected?.tools.length ? (
              <ul className="cc-mini-list">{selected.tools.map((tool, i) => (
                <li key={i}><span>{toolLabel(tool.name)}</span><span className="mono">{selected.t0 != null ? `+${fmtMs(tool.t - selected.t0)}` : "–"}</span></li>
              ))}</ul>
            ) : <p className="cc-empty">Keine Werkzeuge aufgerufen.</p>}
          </section>
          <section>
            <h3>Offene Freigaben</h3>
            {openApprovals.length ? (
              <>
                <ul className="cc-mini-list">{openApprovals.slice(0, 3).map((a) => <li key={a.id}><span>{a.vorschau?.titel || a.werkzeug || "Freigabe"}</span></li>)}</ul>
                <button type="button" className="cc-btn small" onClick={onOpenApprovals}>Zu den Freigaben ({openApprovals.length})</button>
              </>
            ) : <p className="cc-empty">Nichts wartet auf dich.</p>}
          </section>
          <section>
            <h3>Unterbrechen</h3>
            <dl className="cc-kv">
              <dt>Unterbrechungen</dt><dd className="mono">{barge.count}</dd>
              <dt>Stopp (Median)</dt><dd className="mono">{fmtMs(barge.median)}</dd>
              <dt>Züge dieser Sitzung</dt><dd className="mono">{turns.length}</dd>
              <dt>Uhrabgleich</dt><dd className="mono">{telemetry.offset == null ? "läuft" : `± ${fmtMs((telemetry.rttMs ?? 0) / 2)}`}</dd>
            </dl>
          </section>
        </aside>
      </div>

      <form className="cc-transport" aria-label="Transportleiste" onSubmit={(e) => { e.preventDefault(); send(); }}>
        {realtimeMode ? (
          <button type="button" className="cc-tbtn on" onClick={() => void toggleMic()} disabled={!owner || switching}>
            <Glyph state="hoert" /> Echtzeit beenden
          </button>
        ) : ptt ? (
          <button type="button" className={`cc-tbtn ${audio?.isRecording() ? "on" : ""}`} disabled={!owner}
            onPointerDown={(e) => { e.preventDefault(); void pttDown(); }} onPointerUp={pttUp} onPointerLeave={pttUp}
            onKeyDown={(e) => { if ((e.key === " " || e.key === "Enter") && !e.repeat) { e.preventDefault(); void pttDown(); } }}
            onKeyUp={(e) => { if (e.key === " " || e.key === "Enter") pttUp(); }}>
            <Glyph state={audio?.isRecording() ? "hoert" : "bereit"} /> {micOn ? "Halten zum Sprechen" : "Mikro einschalten"} <kbd>Leertaste</kbd>
          </button>
        ) : (
          <button type="button" className={`cc-tbtn ${micOn ? "on" : ""}`} aria-pressed={micOn} onClick={() => void toggleMic()} disabled={!owner}>
            <Glyph state={micOn ? "hoert" : "bereit"} /> {micOn ? "Mikro an" : "Mikro"} <kbd>M</kbd>
          </button>
        )}
        <label className="cc-tinput">
          <span className="cc-sr">Nachricht an Jarvis</span>
          <input ref={inputRef} value={text} maxLength={8000} disabled={!owner} onChange={(e) => setText(e.target.value)}
            placeholder={owner ? "Nachricht an Jarvis …" : "Nur Ansicht – ein anderes Fenster steuert Jarvis"}
            onKeyDown={(e) => {
              if (e.key === "Escape") { e.stopPropagation(); (e.target as HTMLInputElement).blur(); }
            }} />
          <kbd>/</kbd>
        </label>
        <button type="submit" className="cc-tbtn primary" disabled={!owner || !text.trim()}>Senden <kbd>⏎</kbd></button>
        {!realtimeMode && <button type="button" className="cc-tbtn" disabled={!owner || !canCancel} onClick={onStop}>Abbrechen <kbd>Esc</kbd></button>}
        <button type="button" className={`cc-tbtn ${muted ? "on" : ""}`} aria-pressed={muted} onClick={() => onMutedChange(!muted)}>
          {muted ? "Stumm an" : "Stumm"} <kbd>S</kbd>
        </button>
        <button type="button" className="cc-tbtn" onClick={() => setSettingsOpen(true)} aria-haspopup="dialog">Einstellungen</button>
      </form>
      <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} prefs={prefs} onPrefs={onPrefs}
        settings={settings} onSettings={onSettings} onReset={onReset} canEdit={owner} />
    </div>
  );
}
