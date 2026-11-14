import React, {
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from "react";
import { createRoot } from "react-dom/client";
import { AudioEngine } from "./audio";
import { UiSync } from "./sync";
import { Telemetry } from "./command/telemetry";
import { RealtimeSession, flushTranscripts, friendlyRealtimeError, pendingSaveState, type SaveState } from "./command/realtime";
import { loadPrefs, savePrefs, type VoicePrefs } from "./command/SettingsDialog";
import { api, ApiError, setOwnerProof } from "./command/api";
import { onPendingCommand, takePendingCommands } from "./command/commandWindow";
import type {
  Config,
  Log,
  Metrics,
  Role,
  Status,
  Transcript,
} from "./types";
import type { ViewState } from "./world/WorldMap";
import { initialView, viewReducer } from "./view/controller";
import {
  classifyUtterance,
  type ContentView,
  type SecurityName,
} from "./view/intent";
import "./styles.css";

const NewsView = lazy(() => import("./views/NewsView"));
const MarketView = lazy(() => import("./views/MarketView"));
const CamerasView = lazy(() => import("./views/CamerasView"));
const OpsView = lazy(() => import("./views/OpsView"));
const CommandView = lazy(() => import("./views/CommandView"));
const CommandWindow = lazy(() => import("./views/CommandWindow"));
/** Ansichten, die das Command-Center-Fenster ans Dashboard weiterreicht (Karte und Gespräch bleiben dort). */
const FORWARD_LABEL: Partial<Record<ContentView, string>> = {
  news: "Nachrichten", market: "Märkte", cameras: "Kameras", ops: "Systemprotokoll", memory: "Gedächtnis", command: "Dashboard",
};

export function openOpsWindow(room: string): void {
  window.open(`?room=${encodeURIComponent(room)}&screen=ops`, "jarvis-ops", "popup,width=820,height=960");
}

const fallback: Config = {
  demo: true,
  auth_required: false,
  cities: [
    { id: "berlin", name: "Berlin", country: "DE", lat: 52.52, lon: 13.405 },
    {
      id: "new-york",
      name: "New York",
      country: "US",
      lat: 40.7128,
      lon: -74.006,
    },
    { id: "tokyo", name: "Tokyo", country: "JP", lat: 35.6762, lon: 139.6503 },
    {
      id: "singapore",
      name: "Singapore",
      country: "SG",
      lat: 1.3521,
      lon: 103.8198,
    },
  ],
  cameras: [],
};
const initialMetrics: Metrics = {
  uptime_s: null,
  event_loop_lag_ms: null,
  rooms: null,
  clients: null,
  interrupts: null,
  stop_ack_ms: null,
  stt_ms: null,
  tts_ms: null,
  dropped_messages: null,
  jvm_heap_used_mb: null,
  jvm_threads: null,
};
const uid = (prefix: string) =>
  `${prefix}-${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`;
const metric = (value: number | null | undefined, suffix = "") =>
  value == null ? "—" : `${Math.round(value)}${suffix}`;

type ConfigState = {
  config: Config | null;
  tokenView: boolean;
  offline: boolean;
  submitToken: (token: string) => Promise<void>;
};
function useConfig(): ConfigState {
  const [config, setConfig] = useState<Config | null>(null);
  const [tokenView, setTokenView] = useState(false);
  const [offline, setOffline] = useState(false);
  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/config");
      if (response.status === 401) {
        setTokenView(true);
        setOffline(false);
        return;
      }
      if (!response.ok) {
        setConfig(fallback);
        setOffline(true);
        return;
      }
      setConfig((await response.json()) as Config);
      setOffline(false);
    } catch {
      setConfig(fallback);
      setOffline(true);
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const submitToken = useCallback(
    async (token: string) => {
      const response = await fetch("/api/session", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token }),
      });
      if (!response.ok) throw new Error("Token abgelehnt");
      setTokenView(false);
      await load();
    },
    [load],
  );
  return { config, tokenView, offline, submitToken };
}

function AuthScreen({ submit }: { submit: (token: string) => Promise<void> }) {
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  return (
    <div className="auth">
      <div className="auth-card">
        <div className="brand-mark">J</div>
        <div className="eyebrow">SECURE SESSION / CONTROL PLANE</div>
        <h1>ACCESS TOKEN</h1>
        <p>
          Der Server verlangt eine HttpOnly-Session. Token wird nicht im UI
          gespeichert.
        </p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void submit(token).catch((reason) =>
              setError(String(reason.message)),
            );
          }}
        >
          <input
            autoFocus
            type="password"
            value={token}
            onChange={(event) => setToken(event.target.value)}
            placeholder="SESSION TOKEN"
          />
          <button type="submit" className="btn amber">
            SESSION ÖFFNEN
          </button>
        </form>
        {error && <div className="error">{error}</div>}
      </div>
    </div>
  );
}

export function App() {
  const { config, tokenView, offline, submitToken } = useConfig();
  const params = useMemo(() => new URLSearchParams(location.search), []);
  const ops = params.get("screen") === "ops";
  // Command Center: eigenes Fenster mit eigenem Raum. Der Server gibt die Steuerung dem ersten Hauptfenster eines
  // Raums – im Raum des Dashboards wäre dieses Fenster nur Beobachter und dürfte weder sprechen noch senden.
  const cmd = params.get("screen") === "command";
  const room = useMemo(() => {
    const key = cmd ? "jarvis-room-command" : "jarvis-room";
    const existing =
      params.get("room") || localStorage.getItem(key) || uid("room");
    const value = /^[a-zA-Z0-9_-]{8,64}$/.test(existing)
      ? existing
      : uid("room");
    localStorage.setItem(key, value);
    return value;
  }, [params, cmd]);
  const client = useMemo(() => uid(ops ? "ops" : "main"), [ops]);
  const [role, setRole] = useState<Role>("observer");
  const [status, setStatus] = useState<Status>("idle");
  const [metrics, setMetrics] = useState<Metrics>(initialMetrics);
  const [logs, setLogs] = useState<Log[]>([]);
  const [transcripts, setTranscripts] = useState<Transcript[]>([]);
  // Memory v1: welche gespeicherten Erinnerungen für welche Antwort (stream_id) mitgeschickt wurden.
  const [memoryUse, setMemoryUse] = useState<Record<string, { id: string; inhalt: string }[]>>({});
  const turnRef = useRef<string | null>(null);
  const [view, setView] = useState<ViewState | null>(null);
  const [selectedCamera, setSelectedCamera] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [micOn, setMicOn] = useState(false);
  const [forwarded, setForwarded] = useState<string | null>(null);
  const audioWsRef = useRef<WebSocket | null>(null);
  const audioRef = useRef<AudioEngine | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const syncRef = useRef<UiSync | null>(null);
  // Ansicht: zentraler Reducer. Liegt AUSSERHALB des Verbindungs-Effekts, damit ein Ansichtswechsel
  // nie WebSocket, AudioEngine, Mikrofon oder Raum neu initialisiert.
  const [viewModel, dispatchView] = useReducer(viewReducer, initialView);
  const [, setInterruptedAt] = useState(-Infinity);
  // Messung der Sprachkette: lebt so lange wie die Seite (Neuverbinden setzt sie nicht zurück, Neuladen schon).
  const [telemetry] = useState(() => new Telemetry());
  const [muted, setMuted] = useState(false);
  const mutedRef = useRef(false);
  const realtimeRef = useRef<RealtimeSession | null>(null);
  const [voiceMode, setVoiceMode] = useState<"studio" | "echtzeit">("studio");
  // Bleibender Hinweis, wenn der Anbieter das Echtzeit-Gespräch beendet hat (der kurze Fehlerzustand verschwindet nach 8 s).
  const [voiceNotice, setVoiceNotice] = useState<string | null>(null);
  // Speicherstatus der erkannten Echtzeit-Äußerungen (gespeichert / ausstehend / fehlgeschlagen).
  const [transcriptSave, setTranscriptSave] = useState<SaveState>(() => pendingSaveState());
  useEffect(() => { void flushTranscripts(setTranscriptSave); }, []);
  const [voicePrefs, setVoicePrefsState] = useState<VoicePrefs>(loadPrefs);
  const prefsRef = useRef(voicePrefs);
  const applyPrefs = useCallback((engine: AudioEngine | null, prefs: VoicePrefs) => {
    engine?.setPause(prefs.pause);
    engine?.setBarge(prefs.barge);
    engine?.setTalkMode(prefs.talkMode);
  }, []);
  const setVoicePrefs = useCallback((next: VoicePrefs) => {
    prefsRef.current = next;
    setVoicePrefsState(next);
    savePrefs(next);
    applyPrefs(audioRef.current, next);
  }, [applyPrefs]);
  useEffect(() => {
    mutedRef.current = muted;
    audioRef.current?.setMuted(muted);
    realtimeRef.current?.setMuted(muted);
  }, [muted]);
  const directoryRef = useRef<SecurityName[]>([]);
  const retryRef = useRef(0);
  const reconnectTimerRef = useRef<number | undefined>(undefined);
  const closedRef = useRef(false);
  const configValue = config ?? fallback;
  const cameras = useMemo(
    () =>
      configValue.cameras.length
        ? configValue.cameras
        : configValue.cities.map((city) => ({
            id: `mock-${city.id}`,
            name: `${city.name} / Orbital Feed`,
            city_id: city.id,
            kind: "mock" as const,
          })),
    [configValue],
  );

  const addLog = useCallback(
    (message: string, level: "info" | "warn" = "info") =>
      setLogs((old) =>
        [
          {
            id: crypto.randomUUID(),
            time: new Date().toISOString(),
            level,
            message,
          },
          ...old,
        ].slice(0, 80),
      ),
    [],
  );
  const send = useCallback(
    (value: object) => {
      if (wsRef.current?.readyState === WebSocket.OPEN)
        wsRef.current.send(JSON.stringify(value));
      else addLog("Steuerkanal nicht verbunden.", "warn");
    },
    [addLog],
  );
  const stopControl = useCallback(
    (reason: "vad" | "manual" | "new_turn") =>
      send({
        type: "STOP_STREAM",
        stream_id: audioRef.current?.currentStreamId() ?? null,
        reason,
      }),
    [send],
  );

  const applyView = useCallback((next: ViewState) => setView(next), []);
  const interrupt = useCallback(() => {
    // Ohne AudioEngine (Dashboard-Fenster) den laufenden Zug direkt beim Server stoppen.
    if (audioRef.current) audioRef.current.manualStop();
    else if (turnRef.current && wsRef.current?.readyState === WebSocket.OPEN)
      wsRef.current.send(JSON.stringify({ type: "STOP_STREAM", stream_id: turnRef.current, reason: "manual" }));
    setInterruptedAt(performance.now());
  }, []);

  useEffect(() => {
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && role === "owner") interrupt();
    };
    window.addEventListener("keydown", onEscape);
    return () => window.removeEventListener("keydown", onEscape);
  }, [role, interrupt]);

  useEffect(() => {
    if (tokenView || !config) return;
    closedRef.current = false;
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    syncRef.current = new UiSync();
    const unsubscribe = syncRef.current.subscribe((next) => {
      if (next.view) applyView(next.view);
      if (next.cameraId !== undefined) setSelectedCamera(next.cameraId);
      // Das Command-Center-Fenster folgt nicht der Navigation des Dashboards.
      if (next.content && !cmd)
        dispatchView({ type: "sync", view: next.content.view, query: next.content.query });
    });
    const disconnectAudio = () => {
      setMicOn(false);
      setRole("observer");
      const socket = audioWsRef.current;
      audioWsRef.current = null;
      socket?.close();
      audioRef.current?.close();
      audioRef.current = null;
    };
    const connect = () => {
      if (closedRef.current) return;
      const ws = new WebSocket(
        `${protocol}://${location.host}/ws/${room}/control?client=${client}&screen=${ops ? "ops" : "main"}`,
      );
      wsRef.current = ws;
      const current = () => !closedRef.current && wsRef.current === ws;
      ws.onopen = () => {
        if (!current()) return;
        setConnected(true);
        telemetry.setConnected(true);
        retryRef.current = 0;
        // Uhrabgleich: 5 PINGs in schneller Folge, danach alle 5 s (sent_at = performance.now()).
        const ping = () => {
          if (current() && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "PING", sent_at: performance.now() }));
        };
        for (let i = 0; i < 5; i++) window.setTimeout(ping, i * 200);
        const pinger = window.setInterval(() => (current() ? ping() : window.clearInterval(pinger)), 5000);
        addLog(ops ? "Operationskanal verbunden." : "Steuerkanal verbunden.");
      };
      ws.onmessage = (event) => {
        if (!current()) return;
        let message: any;
        try {
          message = JSON.parse(event.data);
        } catch {
          return;
        }
        telemetry.onServer(message);
        switch (message.type) {
          case "WELCOME": {
            const snapshot = message.snapshot;
            setRole(message.role === "owner" ? "owner" : "observer");
            // Besitzer-Ticket für Lead-Schreibzugriffe (nur im Speicher); Beobachter bekommen keins.
            setOwnerProof(room, message.role === "owner" && typeof message.owner_ticket === "string" ? message.owner_ticket : null);
            setStatus(snapshot.status);
            if (snapshot.view) {
              setView(snapshot.view);
            }
            setSelectedCamera(snapshot.selected_camera ?? null);
            setMetrics({ ...initialMetrics, ...(snapshot.metrics ?? {}) });
            setLogs(snapshot.logs ?? []);
            setTranscripts(snapshot.transcripts ?? []);
            addLog(
              message.role === "owner"
                ? "OWNER bestätigt; Audio-Kanal darf geöffnet werden."
                : "Observer-Modus: Steuerung und Audio gesperrt.",
              message.role === "owner" ? "info" : "warn",
            );
            // Sprache gibt es nur im Command-Center-Fenster: das Dashboard öffnet weder Audio-Kanal noch Mikrofon.
            if (message.role === "owner" && cmd && !audioWsRef.current) {
              const audio = new AudioEngine({
                state: setStatus,
                log: addLog,
                stopControl,
                mark: (mark) => telemetry.onMark(mark),
              });
              audio.setMuted(mutedRef.current);
              applyPrefs(audio, prefsRef.current);
              audioRef.current = audio;
              const audioWs = new WebSocket(
                `${protocol}://${location.host}/ws/${room}/audio?client=${client}`,
              );
              audioWs.binaryType = "arraybuffer";
              audioWsRef.current = audioWs;
              audioWs.onopen = () => {
                if (current() && audioWsRef.current === audioWs) {
                  audio.attach(audioWs);
                  addLog("Audio-Kanal verbunden.");
                }
              };
              audioWs.onmessage = (audioEvent) => {
                if (
                  current() &&
                  audioWsRef.current === audioWs &&
                  audioEvent.data instanceof ArrayBuffer
                )
                  audio.receive(audioEvent.data);
              };
              audioWs.onclose = () => {
                if (!current() || audioWsRef.current !== audioWs) return;
                audioWsRef.current = null;
                audio.close();
                audioRef.current = null;
                setMicOn(false);
                setRole("observer");
                addLog(
                  "Audio-Kanal getrennt; Sitzung wird als Observer neu verbunden.",
                  "warn",
                );
                if (ws.readyState === WebSocket.OPEN) ws.close();
              };
            }
            break;
          }
          case "TURN_START":
            turnRef.current = message.stream_id ? String(message.stream_id) : null;
            audioRef.current?.turnStarted(message.stream_id);
            dispatchView({ type: "turn", stream: String(message.stream_id) });
            break;
          case "TOOL":
            if (typeof message.name === "string")
              dispatchView({ type: "tool", stream: message.stream_id ?? null, name: message.name });
            break;
          case "STATE":
            setStatus(message.status);
            break;
          case "TRANSCRIPT":
            // Nur fertige Nutzeräußerungen steuern die Ansicht – nie laufende Modell-Tokens.
            if (message.role === "user" && message.final && typeof message.text === "string")
              dispatchView({
                type: "utterance",
                stream: message.stream_id ?? null,
                intent: classifyUtterance(message.text, directoryRef.current),
              });
            setTranscripts((old) =>
              [...old.filter((item) => item.id !== message.id), message].slice(
                -80,
              ),
            );
            break;
          case "LOG":
            addLog(message.message, message.level);
            break;
          case "MEMORY":
            if (Array.isArray(message.erinnerungen) && message.erinnerungen.length && turnRef.current) {
              const stream = turnRef.current;
              const items = message.erinnerungen
                .filter((e: any) => e && typeof e.id === "string" && typeof e.inhalt === "string")
                .slice(0, 5).map((e: any) => ({ id: e.id, inhalt: e.inhalt.slice(0, 160) }));
              setMemoryUse((old) => ({ ...Object.fromEntries(Object.entries(old).slice(-40)), [stream]: items }));
            }
            break;
          case "METRICS":
            setMetrics((old) => ({ ...old, ...message }));
            break;
          case "VIEW":
            applyView({
              city_id: message.city_id,
              lat: message.lat,
              lon: message.lon,
              height: message.height,
            });
            break;
          case "CAMERA":
            setSelectedCamera(message.camera_id);
            break;
          case "STREAM_STOPPED":
            audioRef.current?.onStopped(message.stream_id);
            dispatchView({ type: "stopped", stream: message.stream_id ?? null });
            setInterruptedAt(performance.now());
            setStatus("idle");
            addLog(
              `Stream gestoppt (${message.reason}, ACK ${metric(message.ack_ms, "ms")}).`,
            );
            break;
          case "RESET_DONE":
            setTranscripts([]);
            setMemoryUse({});
            break;
          case "ERROR":
            addLog(message.message, "warn");
            break;
        }
      };
      ws.onclose = () => {
        if (!current()) return;
        setConnected(false);
        telemetry.setConnected(false);
        setOwnerProof(room, null);  // das Ticket ist serverseitig mit der Verbindung erloschen
        disconnectAudio();
        wsRef.current = null;
        if (!closedRef.current) {
          const wait = Math.min(10000, 500 * 2 ** retryRef.current++);
          addLog(
            `Kanal getrennt; Reconnect in ${wait} ms. Mikrofon bleibt aus.`,
            "warn",
          );
          reconnectTimerRef.current = window.setTimeout(connect, wait);
        }
      };
      ws.onerror = () => {
        if (current()) ws.close();
      };
    };
    connect();
    return () => {
      closedRef.current = true;
      if (reconnectTimerRef.current !== undefined)
        window.clearTimeout(reconnectTimerRef.current);
      unsubscribe();
      syncRef.current?.close();
      syncRef.current = null;
      disconnectAudio();
      wsRef.current?.close();
      wsRef.current = null;
      setConnected(false);
    };
  }, [addLog, applyPrefs, applyView, client, cmd, config, ops, room, stopControl, telemetry, tokenView]);

  const publishUi = useCallback(
    (next: { view?: ViewState; cameraId?: string | null }) =>
      syncRef.current?.publish(next),
    [],
  );
  const chooseCamera = (id: string) => {
    setSelectedCamera(id);
    send({ type: "CAMERA_SELECT", camera_id: id });
    publishUi({ cameraId: id });
  };
  // --- Echtzeit-Modus (ElevenLabs Agents): nie zwei Mikrofone gleichzeitig ---
  const stopRealtime = useCallback(async () => {
    const session = realtimeRef.current;
    realtimeRef.current = null;
    await session?.end();
    telemetry.realtimeState(null);
    setVoiceMode("studio");
  }, [telemetry]);
  const startRealtime = useCallback(async (): Promise<string | null> => {
    if (role !== "owner" || realtimeRef.current) return null;
    setVoiceNotice(null);
    // Studio zuerst vollständig freigeben: laufenden Strom stoppen, Wiedergabe leeren, Mikrofon-Tracks beenden.
    audioRef.current?.manualStop();
    audioRef.current?.stopPlayback();
    audioRef.current?.disableMic();
    setMicOn(false);
    telemetry.realtimeState({ mode: "verbindet", toolRunning: false });
    setVoiceMode("echtzeit");
    const session = new RealtimeSession({
      onMode: (mode) => {
        if (mode === "verbindet") telemetry.realtimeState({ mode, toolRunning: false });
        else telemetry.realtimeMode(mode);
      },
      onUser: (value) => telemetry.realtimeUser(value),
      onAgent: (value) => telemetry.realtimeAgent(value),
      onTool: (name, running) => telemetry.realtimeTool(name, running),
      onInterrupted: () => telemetry.realtimeInterrupted(),
      onSaved: (state) => setTranscriptSave(state),
      onError: (message) => { telemetry.realtimeError(message); addLog(`Echtzeit: ${message}`, "warn"); },
      onEnded: (reason) => {
        if (realtimeRef.current === session) {
          realtimeRef.current = null;
          telemetry.realtimeState(null);
          setVoiceMode("studio");
          // Nicht stumm verschwinden: wer die Sitzung beendet hat, steht als Fehler bzw. im Protokoll.
          if (reason) { telemetry.realtimeError(reason); setVoiceNotice(`${reason} Du bist wieder im Studio-Modus.`); }
          addLog(reason ?? "Echtzeit-Gespräch beendet; Studio-Modus aktiv (Mikro erst nach „Mikro (M)“).", reason ? "warn" : "info");
        }
      },
    });
    realtimeRef.current = session;
    try {
      await session.start(prefsRef.current.pause, mutedRef.current);
      return null;
    } catch (error) {
      // Meldungen des Jarvis-Servers (z. B. fehlende ElevenLabs-Berechtigung) sind schon Klartext; Browserfehler übersetzen.
      const message = error instanceof ApiError ? error.message : friendlyRealtimeError(error);
      const wasCurrent = realtimeRef.current === session;
      if (wasCurrent) realtimeRef.current = null;
      await session.end();
      if (!wasCurrent) return null; // inzwischen vom Nutzer abgebrochen: kein Fehler
      telemetry.realtimeState(null);
      telemetry.realtimeError(`Nicht gestartet. ${message}`); // die Anzeige stellt „Echtzeit:“ voran
      setVoiceMode("studio");
      addLog(`Echtzeit nicht gestartet: ${message}`, "warn");
      return `Echtzeit nicht gestartet: ${message}`;
    }
  }, [addLog, role, telemetry]);
  useEffect(() => () => void realtimeRef.current?.end(), []);
  const getRealtime = useCallback(() => realtimeRef.current, []);
  const resetConversation = useCallback(() => {
    send({ type: "RESET" });
    void api.resetConversation().catch((error) => addLog(`Zurücksetzen fehlgeschlagen: ${error instanceof Error ? error.message : "unbekannt"}`, "warn"));
  }, [addLog, send]);

  const askJarvis = useCallback((raw: string) => {
    const value = raw.trim();
    if (!value || role !== "owner") return;
    if (realtimeRef.current) {
      realtimeRef.current.sendText(value.slice(0, 2000));
      return;
    }
    // Audio-Freigabe anstoßen, aber nie darauf warten: Text muss auch ohne Wiedergabe-Freigabe ankommen.
    void audioRef.current?.unlockPlayback().catch(() => undefined);
    audioRef.current?.stopPlayback();
    stopControl("new_turn");
    telemetry.noteTyped();
    send({ type: "USER_TEXT", text: value.slice(0, 8000) });
  }, [role, send, stopControl, telemetry]);
  // Aufträge, die das Dashboard übergibt: einmal beim Start (sobald dieses Fenster steuert) und danach laufend.
  useEffect(() => {
    if (!cmd || role !== "owner") return;
    for (const text of takePendingCommands()) askJarvis(text);
    return onPendingCommand(askJarvis);
  }, [askJarvis, cmd, role]);

  // Wertpapier-Namen für die Absichtserkennung (eine Quelle: Backend-Verzeichnis).
  useEffect(() => {
    if (!config || tokenView) return;
    fetch("/api/market?verzeichnis=true")
      .then((response) => (response.ok ? response.json() : null))
      .then((body) => {
        if (Array.isArray(body?.verzeichnis)) directoryRef.current = body.verzeichnis;
      })
      .catch(() => undefined);
  }, [config, tokenView]);

  // Ansicht an andere Fenster spiegeln (nur UI-Zustand, nie Audio oder Tokens).
  useEffect(() => {
    if (viewModel.revision === 0 || viewModel.origin === "sync" || ops) return;
    if (cmd) {
      // Karte und Gespräch bleiben im Command Center; alles andere öffnet das Dashboard-Fenster.
      if (viewModel.view === "conversation" || viewModel.view === "world") return;
      syncRef.current?.publish({ content: { view: viewModel.view, query: viewModel.query } });
      setForwarded(`${FORWARD_LABEL[viewModel.view] ?? "Ansicht"} im Dashboard-Fenster geöffnet`);
      dispatchView({ type: "back" });
      return;
    }
    syncRef.current?.publish({ content: { view: viewModel.view, query: viewModel.query } });
  }, [cmd, ops, viewModel.revision, viewModel.origin, viewModel.view, viewModel.query]);
  const toDashboard = useCallback((section: string) => {
    syncRef.current?.publish({ content: { view: "command", query: section } });
    try {
      if (window.opener && !window.opener.closed) window.opener.focus();
      else window.open(location.pathname, "jarvis-dashboard");
    } catch { /* Fokus ist Sache des Browsers; der Abschnitt ist trotzdem umgeschaltet */ }
  }, []);

  const showView = useCallback((next: ContentView, query?: string) => {
    if (next === "conversation") dispatchView({ type: "back" });
    else dispatchView({ type: "manual", view: next, query });
  }, []);
  const back = useCallback(() => dispatchView({ type: "back" }), []);
  if (tokenView) return <AuthScreen submit={submitToken} />;
  if (!config)
    return (
      <div className="loading">
        JARVIS<span>…</span>
      </div>
    );
  if (ops)
    return (
      <div className="shell ops-shell">
        <header className="ops-top">
          <div className="brand-mark">J</div>
          <div>
            <div className="view-eyebrow">Screen 2 · Operations</div>
            <h1 className="view-title">Jarvis</h1>
          </div>
          <span className={`pill ${connected ? "online" : "offline"}`}>
            <i />
            {connected ? "VERBUNDEN" : "GETRENNT"}
          </span>
        </header>
        <Suspense fallback={<div className="loading">…</div>}>
          <OpsView metrics={metrics} transcripts={transcripts} logs={logs} status={status} room={room} />
        </Suspense>
      </div>
    );

  const current = viewModel.view;
  const alert = !connected
    ? "Verbindung zum Jarvis-Server getrennt – Neuverbindung läuft."
    : offline
      ? "Server-Konfiguration nicht erreichbar."
      : null;
  if (cmd)
    return (
      <div className="stage workspace" data-view="command-center" data-demo={config.demo || undefined}>
        <Suspense fallback={<div className="view-loading">…</div>}>
          <CommandWindow
            telemetry={telemetry} audio={audioRef.current} role={role} connected={connected} transcripts={transcripts}
            micOn={micOn} onMicChange={setMicOn} muted={muted} onMutedChange={setMuted} onAsk={askJarvis} onStop={interrupt} onLog={addLog}
            voiceMode={voiceMode} onStartRealtime={startRealtime} onStopRealtime={stopRealtime} getRealtime={getRealtime}
            prefs={voicePrefs} onPrefs={setVoicePrefs} onReset={resetConversation} room={room} memoryUse={memoryUse}
            mapQuery={current === "world" ? viewModel.query ?? "" : null} onCloseMap={back} onDashboard={toDashboard}
            forwarded={forwarded} demo={config.demo} voiceNotice={voiceNotice}
          />
        </Suspense>
        {alert && <div className="ws-alert" role="status">{alert}</div>}
      </div>
    );
  // Dashboard: eine Schiene für alles. „Mehr“-Ansichten laufen im selben Rahmen; Sprache liegt im Command Center.
  const more =
    current === "news" ? <NewsView query={viewModel.query} onBack={back} />
    : current === "market" ? <MarketView query={viewModel.query} onBack={back} onPick={(symbol) => showView("market", symbol)} />
    : current === "cameras" ? <CamerasView cameras={cameras} selected={selectedCamera} onSelect={chooseCamera} demo={config.demo} onBack={back} />
    : current === "ops" ? <OpsView metrics={metrics} transcripts={transcripts} logs={logs} status={status} room={room} onBack={back} onOpenSecond={() => openOpsWindow(room)} />
    : null;
  return (
    <div className="stage workspace" data-view={current} data-demo={config.demo || undefined}>
      <Suspense fallback={<div className="view-loading">…</div>}>
        <CommandView
          query={current === "command" ? viewModel.query : undefined}
          section={current === "world" ? "welt" : current === "memory" ? "gedaechtnis" : undefined}
          worldQuery={current === "world" ? viewModel.query : undefined}
          role={role}
          connected={connected}
          onAsk={askJarvis}
          onView={(next) => showView(next)}
          voiceMode={voiceMode}
          transcriptSave={transcriptSave}
          onRetryTranscripts={() => void flushTranscripts(setTranscriptSave)}
          more={more ? { id: current, node: <Suspense fallback={<div className="view-loading">…</div>}>{more}</Suspense> } : null}
        />
      </Suspense>
      {config.demo && <div className="demo-mark">Demo</div>}
      {alert && <div className="ws-alert" role="status">{alert}</div>}
    </div>
  );
}

export function mount(root: HTMLElement) {
  createRoot(root).render(<App />);
}
