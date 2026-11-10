/**
 * Echtzeit-Modus (ElevenLabs Agents), übernommen aus der früheren Oberfläche auf 8765.
 * Die Sitzung holt sich ihr eigenes Mikrofon – der Studio-Modus muss sein Mikrofon vorher freigeben (nie zwei zugleich).
 * Werkzeuge laufen über /api/realtime/tool im Freigabe-Kontext „echtzeit“; Jobs über /api/jobs/extern.
 */
import { api } from "./api";

type Conversation = {
  endSession(): Promise<void>;
  sendUserMessage(text: string): void;
  setVolume(options: { volume: number }): void;
  getInputVolume(): number;
  getOutputVolume(): number;
};
type ClientModule = { Conversation: { startSession(options: Record<string, unknown>): Promise<Conversation> } };

let clientPromise: Promise<ClientModule> | null = null;
/** Lädt den mitgelieferten ElevenLabs-Client (MIT, /vendor/elevenlabs-client.js) erst bei Bedarf. */
export function loadClient(): Promise<ClientModule> {
  const existing = (window as unknown as { ElevenLabsClient?: ClientModule }).ElevenLabsClient;
  if (existing) return Promise.resolve(existing);
  clientPromise ??= new Promise<ClientModule>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "/vendor/elevenlabs-client.js";
    script.async = true;
    script.onload = () => {
      const loaded = (window as unknown as { ElevenLabsClient?: ClientModule }).ElevenLabsClient;
      if (loaded) resolve(loaded);
      else reject(new Error("ElevenLabs-Client konnte nicht geladen werden."));
    };
    script.onerror = () => {
      clientPromise = null;
      reject(new Error("ElevenLabs-Client (/vendor/elevenlabs-client.js) nicht erreichbar."));
    };
    document.head.append(script);
  });
  return clientPromise;
}

/** Speicherstatus der erkannten Äußerungen dieser Sitzung plus Rückstand aus früheren (noch nicht übertragen). */
export type SaveState = { gespeichert: number; ausstehend: number; fehlgeschlagen: number };
type QueueItem = { ref: string; rolle: "user" | "agent"; text: string; versuche: number };
const QUEUE_KEY = "jarvis-transkript-warteschlange";
let saved = 0;
let flushing = false;

function readQueue(): QueueItem[] {
  try {
    const raw = JSON.parse(localStorage.getItem(QUEUE_KEY) ?? "[]");
    return Array.isArray(raw) ? raw.filter((q) => q && typeof q.ref === "string" && typeof q.text === "string").slice(-400) : [];
  } catch { return []; }
}
function writeQueue(items: QueueItem[]): void {
  try { localStorage.setItem(QUEUE_KEY, JSON.stringify(items.slice(-400))); } catch { /* Speicher voll/gesperrt: Rückstand bleibt nur in dieser Sitzung */ }
}
let queue: QueueItem[] = readQueue();
const stateOf = (): SaveState => ({
  gespeichert: saved,
  ausstehend: queue.filter((q) => q.versuche < 3).length,
  fehlgeschlagen: queue.filter((q) => q.versuche >= 3).length,
});
export const pendingSaveState = (): SaveState => stateOf();

/**
 * Überträgt wartende Äußerungen in das Gesprächsarchiv. Jede Äußerung liegt zuerst lokal in der Warteschlange
 * (übersteht Verbindungsabbruch und Neuladen) und wird erst nach bestätigtem Speichern entfernt. Die Referenz
 * macht Wiederholungen ungefährlich: derselbe Eintrag wird serverseitig nie doppelt angelegt.
 */
export async function flushTranscripts(notify: (state: SaveState) => void, retryFailed = true): Promise<void> {
  if (retryFailed) queue = queue.map((q) => ({ ...q, versuche: Math.min(q.versuche, 2) }));
  if (flushing) return; // der laufende Durchlauf liest die Warteschlange nach jedem Eintrag neu und nimmt Neues mit
  flushing = true;
  let stuck = false;
  try {
    // Immer den ältesten sendbaren Eintrag zuerst (Reihenfolge Nutzer → Jarvis bleibt erhalten); neu eingereihte
    // Äußerungen werden im selben Durchlauf mitgenommen.
    for (let item = queue.find((q) => q.versuche < 3); item; item = queue.find((q) => q.versuche < 3)) {
      const current = item;
      try {
        await api.transcript(current.rolle, current.text, current.ref);
        queue = queue.filter((q) => q.ref !== current.ref);
        saved++;
      } catch (error) {
        const status = (error as { status?: number })?.status;
        // 400 = Äußerung wird vom Server abgelehnt (leer/zu lang): nicht endlos wiederholen.
        queue = status === 400 ? queue.filter((q) => q.ref !== current.ref)
          : queue.map((q) => (q.ref === current.ref ? { ...q, versuche: q.versuche + 1 } : q));
        if (status !== 400) { stuck = true; break; } // Server gerade nicht erreichbar: gleich erneut versuchen
      } finally {
        writeQueue(queue);
        notify(stateOf());
      }
    }
  } finally {
    flushing = false;
    notify(stateOf());
    // Automatischer neuer Versuch, solange etwas Sendbares wartet (nach 3 Fehlversuchen: Knopf „erneut senden“).
    if (stuck && queue.some((q) => q.versuche < 3)) window.setTimeout(() => void flushTranscripts(notify, false), 4000);
  }
}
function enqueue(rolle: "user" | "agent", text: string, notify: (state: SaveState) => void): void {
  const ref = `rt-${crypto.randomUUID().replaceAll("-", "").slice(0, 24)}`;
  queue.push({ ref, rolle, text: text.slice(0, 8000), versuche: 0 });
  writeQueue(queue);
  notify(stateOf());
  void flushTranscripts(notify, false);
}

export type RealtimeCallbacks = {
  /** Speicherstatus der Äußerungen (jede erkannte Äußerung wird sofort ins Gesprächsarchiv geschrieben). */
  onSaved?: (state: SaveState) => void;
  onMode: (mode: "verbindet" | "listening" | "speaking") => void;
  onUser: (text: string) => void;
  onAgent: (text: string) => void;
  onTool: (name: string, running: boolean) => void;
  onInterrupted: () => void;
  onError: (message: string) => void;
  /** Sitzung beendet; `reason` erklärt, wer sie beendet hat (Anbieter, Fehler, du). */
  onEnded: (reason: string | null) => void;
};

/** Grund eines Sitzungsendes laut ElevenLabs-Client in Klartext. */
export function endReason(details: unknown): string | null {
  const d = (details ?? {}) as { reason?: string; message?: string; context?: { reason?: string } };
  if (d.reason === "user") return null;
  const raw = `${d.message ?? ""} ${d.context?.reason ?? ""}`.trim();
  if (d.reason === "agent")
    return /duration|max/i.test(raw)
      ? "ElevenLabs hat das Gespräch nach Erreichen der Höchstdauer beendet."
      : `ElevenLabs hat das Gespräch beendet${raw ? ` (${raw.slice(0, 120)})` : ""}.`;
  if (d.reason === "error") return `Verbindung zu ElevenLabs abgebrochen${raw ? `: ${raw.slice(0, 120)}` : ""}.`;
  return null;
}

export function friendlyRealtimeError(error: unknown): string {
  const name = (error as { name?: string })?.name ?? "";
  const message = String((error as { message?: string })?.message ?? error ?? "");
  if (name === "NotAllowedError" || /permission|not allowed/i.test(message))
    return "Mikrofon ist blockiert. Erlaube den Zugriff über das Schloss-Symbol in der Adressleiste.";
  if (name === "NotFoundError") return "Kein Mikrofon gefunden.";
  if (/401|403|unauthori[sz]ed|invalid api key/i.test(message)) return "ElevenLabs lehnt den Schlüssel ab – ELEVENLABS_API_KEY prüfen.";
  if (/quota|402|credits/i.test(message)) return "ElevenLabs-Kontingent erschöpft.";
  return message.slice(0, 200) || "Verbindung zu ElevenLabs fehlgeschlagen.";
}

export class RealtimeSession {
  private conversation: Conversation | null = null;
  private job: { id: string | null; ended: boolean; waiting: boolean } | null = null;
  private endTimer: number | undefined;
  private mode: "listening" | "speaking" = "listening";
  private tools = 0;
  private stopped = false;

  constructor(private readonly callbacks: RealtimeCallbacks) {}

  get active(): boolean {
    return this.conversation !== null;
  }
  inputVolume(): number {
    try { return this.conversation?.getInputVolume() ?? 0; } catch { return 0; }
  }
  outputVolume(): number {
    try { return this.conversation?.getOutputVolume() ?? 0; } catch { return 0; }
  }
  setMuted(muted: boolean): void {
    try { this.conversation?.setVolume({ volume: muted ? 0 : 1 }); } catch { /* Sitzung beendet */ }
  }
  sendText(text: string): void {
    this.conversation?.sendUserMessage(text);
  }

  async start(pause: number, muted: boolean): Promise<void> {
    this.stopped = false;
    this.callbacks.onMode("verbindet");
    const [client, session] = await Promise.all([loadClient(), api.realtimeSession(pause)]);
    if (this.stopped) return;
    const clientTools: Record<string, (params: unknown) => Promise<string>> = {};
    for (const name of session.werkzeuge ?? []) {
      clientTools[name] = async (params) => {
        if (!this.job) await this.jobStart("Sprachauftrag");
        window.clearTimeout(this.endTimer);
        const jobId = this.job?.id ?? null;
        this.tools++;
        this.callbacks.onTool(name, true);
        try {
          return (await api.realtimeTool(name, params, jobId)).ergebnis || "Kein Ergebnis.";
        } catch {
          return "Fehler: Werkzeug nicht erreichbar.";
        } finally {
          this.tools = Math.max(0, this.tools - 1);
          this.callbacks.onTool(name, false);
        }
      };
    }
    this.conversation = await client.Conversation.startSession({
      signedUrl: session.signed_url,
      connectionType: "websocket",
      clientTools,
      onConnect: () => this.callbacks.onMode("listening"),
      onDisconnect: (details: unknown) => {
        this.conversation = null;
        void this.jobEnd("COMPLETED");
        this.callbacks.onEnded(this.stopped ? null : endReason(details));
      },
      onError: (message: unknown) => this.callbacks.onError(friendlyRealtimeError(message)),
      onModeChange: ({ mode }: { mode: "listening" | "speaking" }) => {
        this.mode = mode;
        this.callbacks.onMode(mode);
        window.clearTimeout(this.endTimer);
        // Auftrag gilt als erledigt, wenn er ausgeredet hat und kurz nichts mehr passiert.
        if (mode === "listening" && this.job)
          this.endTimer = window.setTimeout(() => { if (this.mode === "listening" && !this.tools) void this.jobEnd("COMPLETED"); }, 2500);
      },
      onMessage: (payload: { role?: string; source?: string; message?: string }) => {
        const role = payload.role || (payload.source === "ai" ? "agent" : "user");
        const text = String(payload.message ?? "");
        if (!text) return;
        // Erst sichern, dann anzeigen: Bricht die Sitzung ab, steht das Erkannte schon im Archiv bzw. in der Warteschlange.
        enqueue(role === "user" ? "user" : "agent", text, this.callbacks.onSaved ?? (() => undefined));
        if (role === "user") {
          void this.jobStart(text);
          this.callbacks.onUser(text);
        } else this.callbacks.onAgent(text);
      },
      onInterruption: () => {
        this.callbacks.onInterrupted();
        void this.jobEnd("CANCELLED");
      },
    });
    if (this.stopped) {
      await this.end();
      return;
    }
    this.setMuted(muted);
  }

  async end(): Promise<void> {
    this.stopped = true;
    window.clearTimeout(this.endTimer);
    const conversation = this.conversation;
    this.conversation = null;
    await this.jobEnd("COMPLETED");
    try { await conversation?.endSession(); } catch { /* bereits getrennt */ }
  }

  private async jobStart(text: string): Promise<void> {
    await this.jobEnd("COMPLETED"); // vorheriger Auftrag ist mit der neuen Frage abgeschlossen
    const job = { id: null as string | null, ended: false, waiting: false };
    this.job = job;
    try {
      const result = await api.externJob({ aktion: "start", titel: text.slice(0, 90) });
      job.id = result.job?.id ?? null;
    } catch { /* Job-Anzeige ist Komfort; das Gespräch läuft weiter */ }
  }

  private async jobEnd(status: "COMPLETED" | "CANCELLED" | "FAILED"): Promise<void> {
    const job = this.job;
    if (!job || job.ended) return;
    job.ended = true;
    this.job = null;
    window.clearTimeout(this.endTimer);
    if (job.id) {
      try { await api.externJob({ aktion: "ende", id: job.id, status }); } catch { /* bereits beendet */ }
    }
  }
}
