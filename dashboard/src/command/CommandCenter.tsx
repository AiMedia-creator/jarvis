import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import type { AudioEngine } from "../audio";
import type { Role, Transcript } from "../types";
import { api, ATTACHMENT_ACCEPT, MAX_ATTACHMENT_BYTES, type AgentJob, type Approval, type Automations, type Platform, type Settings } from "./api";
import { Icon } from "./icons";
import { duration, isActive, jobState } from "./AgentCenter";
import { remaining } from "./model";
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
  /** Nur für lesbare Werkzeugnamen. */
  platform?: Platform | null;
  /** Echte Agenten-Jobs (null = noch nicht geladen). */
  jobs?: AgentJob[] | null;
  jobsError?: string | null;
  busyJob?: string | null;
  onOpenJob?: (id?: string) => void;
  onCancelJob?: (id: string) => void;
  /** Erhöht sich, wenn die Eingabe den Fokus bekommen soll (⌘K). */
  focusSignal?: number;
  /** Raum (= Gespräch) für Dateianhänge; ohne Raum ist Anhängen aus. */
  room?: string;
};

type PendingFile = { key: string; name: string; size: number; status: "laedt" | "bereit" | "fehler"; id?: string; info?: string };
const fmtSize = (n: number) => n < 1024 * 1024 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1024 / 1024).toFixed(1).replace(".", ",")} MB`;

/** Einstiege für ein leeres Gespräch: füllen nur das Feld. */
const STARTERS = [
  "Was steht heute an? Gib mir die drei wichtigsten Punkte.",
  "Welche Freigaben sind offen?",
  "Lege einen Auftrag an: ",
];

/** Command Center: Auftrag übergeben (Sprache oder Text), Antwort lesen, laufende Vorgänge und Freigaben im Blick.
 *  Die Messung der Sprachkette bleibt vollständig erhalten, liegt aber hinter „Diagnose“. */
export function CommandCenter({
  telemetry, audio, role, connected, transcripts, micOn, onMicChange, muted, onMutedChange, onAsk, onStop, onLog,
  automations, approvals, onOpenApprovals, settings = null, onSettings = () => undefined, voiceMode = "studio",
  onStartRealtime = async () => undefined, onStopRealtime = async () => undefined, getRealtime = () => null,
  prefs = { pause: 1.2, barge: "frei", talkMode: "frei" }, onPrefs = () => undefined, onReset = () => undefined,
  platform = null, jobs = null, jobsError = null, busyJob = null,
  onOpenJob = () => undefined, onCancelJob = () => undefined, focusSignal = 0, room,
}: CommandCenterProps) {
  const toolLabel = (name: string) => platform?.faehigkeiten.find((c) => c.name === name)?.titel ?? name;
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
  const [diag, setDiag] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  useEffect(() => { if (focusSignal > 0) inputRef.current?.focus(); }, [focusSignal]);
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

  // --- Dateianhänge: sofort hochladen (Prüfung auf dem Server), mit der nächsten Nachricht senden ---
  const [files, setFiles] = useState<PendingFile[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const uploading = files.some((f) => f.status === "laedt");
  const ready = files.filter((f) => f.status === "bereit");
  const canAttach = owner && !!room;
  const addFiles = useCallback((list: FileList | File[]) => {
    if (!room) return;
    for (const file of Array.from(list)) {
      const key = `${file.name}-${file.size}-${Math.random().toString(36).slice(2, 8)}`;
      if (file.size > MAX_ATTACHMENT_BYTES) {
        setFiles((old) => [...old, { key, name: file.name, size: file.size, status: "fehler", info: "Zu groß (höchstens 10 MB)." }]);
        continue;
      }
      setFiles((old) => [...old, { key, name: file.name, size: file.size, status: "laedt" }]);
      api.uploadAttachment(room, file)
        .then(({ anhang }) => setFiles((old) => old.map((f) => f.key === key
          ? { ...f, status: "bereit", id: anhang.id, info: anhang.hinweis || `${anhang.art}${anhang.detail ? ` · ${anhang.detail}` : ""}` } : f)))
        .catch((error) => setFiles((old) => old.map((f) => f.key === key
          ? { ...f, status: "fehler", info: error instanceof Error ? error.message : "Hochladen fehlgeschlagen." } : f)));
    }
  }, [room]);
  const removeFile = useCallback((file: PendingFile) => {
    setFiles((old) => old.filter((f) => f.key !== file.key));
    if (file.id && room) void api.removeAttachment(room, file.id).catch(() => setNotice("Anhang konnte auf dem Server nicht entfernt werden – er verfällt nach 30 Minuten."));
  }, [room]);

  const send = useCallback(() => {
    const value = text.trim() || (ready.length ? "Bitte sieh dir die angehängten Dateien an." : "");
    if (!value || !owner || uploading) return;
    onAsk(value);
    setText("");
    setFiles([]); // der Server hat die Anhänge mit dieser Nachricht übernommen
  }, [onAsk, owner, ready.length, text, uploading]);

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
      else if (key === "d" || key === "D") { event.preventDefault(); setDiag((d) => !d); }
      else if (!diag) return;
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
  }, [diag, muted, onMutedChange, ptt, selected?.id, settingsOpen, toggleMic, turns]);

  const jarvis = automations?.agenten.find((a) => a.id === "jarvis");
  const speech = automations?.sprache ?? null;
  const stagesNow = selected ? telemetry.stages(selected) : null;
  const activeStage = STATE_STAGE[derived.active] ?? null;
  const barge = telemetry.bargeStats();
  // Gespräch: Studio-Züge kommen vom Server, Echtzeit-Züge aus der Sitzung des Anbieters (getrennt beschriftet, nie vermischt).
  const realtimeLines = useMemo(() => turns.filter((t) => t.source === "realtime").slice().reverse()
    .flatMap((t) => (t.texts ?? []).map((x, i) => ({ id: `${t.id}-${i}`, role: x.role, text: x.text, final: true }))),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [version, turns]);
  const lineCount = transcripts.length + realtimeLines.length;
  const lastText = transcripts[transcripts.length - 1]?.text ?? "";
  useEffect(() => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [lineCount, lastText]);
  const activeJobs = (jobs ?? []).filter(isActive);
  const lastTools = turns[0]?.tools ?? [];
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

  const stateDetail = segmentContent(derived.active);
  const micHint = realtimeMode ? "Echtzeit-Gespräch läuft" : micOn ? (ptt ? "Mikro an · Push-to-talk" : "Mikro an") : "Mikro aus";

  return (
    <div className={`cc-center ${derived.active === "offline" ? "is-offline" : ""} ${diag ? "with-diag" : ""}`} data-state={derived.active}>
      <header className="cc-bar">
        <div className={`cc-now ${derived.active}`}>
          <Glyph state={derived.active} />
          <strong>{LABEL[derived.active]}</strong>
          {stateDetail}
          <span className="cc-now-mic">{micHint}{permission === "denied" ? " · blockiert" : ""}</span>
        </div>
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
        <button type="button" className={`cc-btn small ${diag ? "pressed" : ""}`} aria-expanded={diag} aria-controls="cc-diag" onClick={() => setDiag((d) => !d)}>
          Diagnose <kbd>D</kbd>
        </button>
        <button type="button" className="cc-btn small" onClick={() => setSettingsOpen(true)} aria-haspopup="dialog">Einstellungen</button>
      </header>
      <p className="cc-sr" role="status" aria-live="polite">Zustand: {LABEL[derived.active]}</p>
      {errorText && <p className="cc-alert" role="alert">{errorText}</p>}
      {notice && <p className="cc-alert soft" role="alert">{notice} <button type="button" className="cc-link" onClick={() => setNotice(null)}>OK</button></p>}

      <div className="cc-center-grid">
        <div className="cc-center-main">
          <section className="cc-thread" ref={threadRef} aria-label="Gespräch mit Jarvis" aria-live="polite" tabIndex={0}
            onDragOver={(e) => { if (canAttach && e.dataTransfer.types.includes("Files")) e.preventDefault(); }}
            onDrop={(e) => { if (!canAttach || !e.dataTransfer.files.length) return; e.preventDefault(); addFiles(e.dataTransfer.files); }}>
            {lineCount === 0 ? (
              <div className="cc-thread-empty">
                <h2>Was soll Jarvis erledigen?</h2>
                <p>{owner ? "Sprich oder schreib. Jarvis arbeitet mit seinen Agenten und fragt dich, bevor etwas nach außen geht."
                  : "Dieses Fenster ist eine Ansicht – ein anderes Fenster steuert Jarvis."}</p>
                {owner && (
                  <ul aria-label="Einstiege – füllen das Feld, senden nichts">
                    {STARTERS.map((starter) => (
                      <li key={starter}><button type="button" onClick={() => { setText(starter); inputRef.current?.focus(); }}>{starter.trim()}{starter.endsWith(" ") ? " …" : ""}</button></li>
                    ))}
                  </ul>
                )}
              </div>
            ) : (
              <>
                {transcripts.map((t) => (
                  <p key={t.id} className={`cc-line ${t.role} ${t.final ? "" : "pending"}`}><b>{t.role === "user" ? "Du" : "Jarvis"}</b>{t.text}</p>
                ))}
                {realtimeLines.length > 0 && <p className="cc-thread-mark">Echtzeit-Gespräch</p>}
                {realtimeLines.map((t) => (
                  <p key={t.id} className={`cc-line ${t.role}`}><b>{t.role === "user" ? "Du" : "Jarvis"}</b>{t.text}</p>
                ))}
              </>
            )}
          </section>

          {diag && (
            <section className="cc-diag" id="cc-diag" aria-label="Diagnose der Sprachkette">
              <div className="cc-hdr-facts" aria-label="Was Jarvis gerade nutzt">
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
              </div>
              <ol className="cc-states" aria-label="Zustand">
                {UI_STATES.map((state) => (
                  <li key={state} className={`cc-seg ${state} ${state === derived.active ? "active" : ""}`} aria-current={state === derived.active ? "step" : undefined}>
                    <Glyph state={state} />
                    <span>{LABEL[state]}</span>
                    {state !== derived.active && derived.also.includes(state) ? <i className="cc-side-dot" title="gilt gleichzeitig" /> : null}
                  </li>
                ))}
              </ol>
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
              <SignalBand source={bandSource} connected={connected} disconnectedAt={telemetry.disconnectedAt} reduced={reduced} redrawKey={version} />
              <TurnTape telemetry={telemetry} selectedId={selected?.id ?? null} onSelect={setSelectedId}
                details={details} onToggleDetails={() => setDetails((d) => !d)} now={now} />
              <dl className="cc-kv cc-diag-kv">
                <dt>Unterbrechungen</dt><dd className="mono">{barge.count}</dd>
                <dt>Stopp (Median)</dt><dd className="mono">{fmtMs(barge.median)}</dd>
                <dt>Züge dieser Sitzung</dt><dd className="mono">{turns.length}</dd>
                <dt>Uhrabgleich</dt><dd className="mono">{telemetry.offset == null ? "läuft" : `± ${fmtMs((telemetry.rttMs ?? 0) / 2)}`}</dd>
              </dl>
            </section>
          )}
        </div>

        <aside className="cc-center-side" aria-label="Vorgänge">
          <section className={openApprovals.length ? "attn" : ""}>
            <h3>Wartet auf dich</h3>
            {openApprovals.length ? (
              <>
                <ul className="cc-mini-list">{openApprovals.slice(0, 3).map((a) => (
                  <li key={a.id}><span>{a.vorschau?.titel || a.werkzeug || "Freigabe"}</span><span className="mono">{remaining(a.laeuft_ab)}</span></li>
                ))}</ul>
                <button type="button" className="cc-btn small accent" onClick={onOpenApprovals}>Freigaben entscheiden ({openApprovals.length})</button>
              </>
            ) : <p className="cc-empty">Nichts wartet auf dich.</p>}
          </section>
          <section>
            <h3>Laufende Vorgänge</h3>
            {jobs == null ? <p className="cc-empty">{jobsError ?? "Lädt …"}</p> : activeJobs.length === 0 ? (
              <p className="cc-empty">Kein Agent arbeitet gerade.</p>
            ) : (
              <ul className="cc-ops">
                {activeJobs.slice(0, 5).map((j) => (
                  <li key={j.id}>
                    <button type="button" className="cc-op" onClick={() => onOpenJob(j.id)}>
                      <span className={`ac-dot ${j.status}`} aria-hidden="true" />
                      <span className="cc-op-main"><strong>{j.titel}</strong>
                        <span>{j.agent.name} · {j.status === "WAITING_FOR_APPROVAL" ? "wartet auf deine Freigabe" : `${jobState(j).label.toLowerCase()} seit ${duration(Date.now() / 1000 - j.start)}`}
                          {j.schritte_gesamt > 0 ? ` · Schritt ${j.schritte_fertig}/${j.schritte_gesamt}` : ""}</span></span>
                    </button>
                    {owner && j.abbrechbar && j.status !== "WAITING_FOR_APPROVAL" && (
                      <button type="button" className="cc-btn small" disabled={busyJob === j.id} onClick={() => onCancelJob(j.id)}
                        aria-label={`Abbrechen: ${j.titel}`}>Abbrechen</button>
                    )}
                  </li>
                ))}
              </ul>
            )}
            <button type="button" className="cc-link" onClick={() => onOpenJob()}>Alle Jobs und Ergebnisse</button>
          </section>
          <section>
            <h3>Werkzeuge im letzten Zug</h3>
            {lastTools.length ? (
              <ul className="cc-mini-list">{lastTools.map((tool, i) => (
                <li key={i}><span>{toolLabel(tool.name)}</span><span className="mono">{turns[0].t0 != null ? `+${fmtMs(tool.t - turns[0].t0)}` : "–"}</span></li>
              ))}</ul>
            ) : <p className="cc-empty">Keine Werkzeuge aufgerufen.</p>}
          </section>
        </aside>
      </div>

      <form className="cc-transport" aria-label="Eingabe" onSubmit={(e) => { e.preventDefault(); send(); }}>
        {files.length > 0 && (
          <ul className="cc-files" aria-label="Anhänge für die nächste Nachricht" aria-live="polite">
            {files.map((f) => (
              <li key={f.key} className={`cc-file ${f.status}`}>
                <Icon name="attach" />
                <span className="cc-file-main"><strong>{f.name}</strong>
                  <span>{f.status === "laedt" ? "wird geprüft …" : f.status === "fehler" ? f.info : `${fmtSize(f.size)}${f.info ? ` · ${f.info}` : ""}`}</span></span>
                <button type="button" className="cc-file-x" onClick={() => removeFile(f)} aria-label={`Anhang entfernen: ${f.name}`}>
                  <Icon name="close" /></button>
              </li>
            ))}
            {ready.length > 0 && <li className="cc-file-note">Geht mit der nächsten Nachricht an Jarvis – auch bei Sprache.</li>}
          </ul>
        )}
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
        <input ref={fileInput} type="file" multiple accept={ATTACHMENT_ACCEPT} hidden
          onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
        <button type="button" className="cc-tbtn cc-attach" onClick={() => fileInput.current?.click()} disabled={!canAttach}
          aria-label="Datei anhängen (PDF, TXT, CSV, DOCX, XLSX, Bilder)" title="Datei anhängen (PDF, TXT, CSV, DOCX, XLSX, Bilder; höchstens 10 MB)">
          <Icon name="attach" /></button>
        <button type="submit" className="cc-tbtn primary" disabled={!owner || uploading || (!text.trim() && !ready.length)}>
          {uploading ? "Prüfe Datei …" : "Senden"} <kbd>⏎</kbd></button>
        {!realtimeMode && <button type="button" className="cc-tbtn" disabled={!owner || !canCancel} onClick={onStop}>Abbrechen <kbd>Esc</kbd></button>}
        <button type="button" className={`cc-tbtn ${muted ? "on" : ""}`} aria-pressed={muted} onClick={() => onMutedChange(!muted)}>
          {muted ? "Stumm an" : "Stumm"} <kbd>S</kbd>
        </button>
      </form>
      <SettingsDialog open={settingsOpen} onClose={() => setSettingsOpen(false)} prefs={prefs} onPrefs={onPrefs}
        settings={settings} onSettings={onSettings} onReset={onReset} canEdit={owner} />
    </div>
  );
}
