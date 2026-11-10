// @vitest-environment happy-dom
/**
 * Integrationstest der echten App: Ansichtswechsel dürfen Steuer-/Audio-WebSocket und AudioEngine nie neu
 * aufbauen; verspätete Ereignisse eines abgebrochenen Zugs öffnen nichts; Start = Arbeitsbereich (Dashboard).
 */
import { act } from "react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

const audio: { instances: any[] } = vi.hoisted(() => ({ instances: [] }));
vi.mock("./audio", () => ({
  AudioEngine: class {
    close = vi.fn();
    turnStarted = vi.fn();
    onStopped = vi.fn();
    manualStop = vi.fn();
    stopPlayback = vi.fn();
    constructor() {
      audio.instances.push(this);
    }
    attach() {}
    receive() {}
    currentStreamId() {
      return null;
    }
    unlockPlayback() {
      return Promise.resolve();
    }
    levels() {
      return { input: 0, output: 0 };
    }
    setMuted = vi.fn();
    setPause = vi.fn();
    setBarge = vi.fn();
    setTalkMode = vi.fn();
    isRecording() {
      return false;
    }
    isAudible() {
      return false;
    }
    meter() {
      return { input: null, output: null, muted: false };
    }
  },
}));
vi.mock("./views/NewsView", () => ({ default: ({ query }: { query?: string }) => <div className="view-frame" data-test="news">NEWS {query ?? ""}</div> }));
vi.mock("./views/MarketView", () => ({ default: ({ query }: { query?: string }) => <div className="view-frame" data-test="market">MARKET {query ?? ""}</div> }));
vi.mock("./views/CamerasView", () => ({ default: () => <div className="view-frame" data-test="cameras">CAMERAS</div> }));

class FakeSocket {
  static OPEN = 1;
  static CLOSED = 3;
  static all: FakeSocket[] = [];
  readyState = 0;
  binaryType = "";
  sent: string[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  constructor(readonly url: string) {
    FakeSocket.all.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
  }
  open() {
    this.readyState = 1;
    this.onopen?.();
  }
  emit(message: object) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }
}

const flush = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
const stage = () => document.querySelector(".stage") as HTMLElement;
const user = (control: FakeSocket, stream: string, text: string) =>
  control.emit({ type: "TRANSCRIPT", id: `user-${stream}`, role: "user", text, stream_id: stream, final: true });

beforeAll(() => {
  // Node 26 bringt ein eigenes localStorage mit, das ohne --localstorage-file undefined ist und das von happy-dom überdeckt.
  if (!globalThis.localStorage) {
    const data = new Map<string, string>();
    Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
      getItem: (k: string) => data.get(k) ?? null, setItem: (k: string, v: string) => void data.set(k, String(v)),
      removeItem: (k: string) => void data.delete(k), clear: () => data.clear(), key: (i: number) => [...data.keys()][i] ?? null,
      get length() { return data.size; } } });
  }
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  (globalThis as any).WebSocket = FakeSocket;
  (globalThis as any).fetch = vi.fn(async (url: string) => {
    const body = url.startsWith("/api/config")
      ? { demo: true, auth_required: false, cities: [], cameras: [] }
      : url.startsWith("/api/market?verzeichnis")
        ? { verzeichnis: [{ symbol: "AAPL", name: "Apple Inc.", aliases: ["apple"] }] }
        : { facts: [], episodes: [], stats: {} };
    return { ok: true, status: 200, json: async () => body } as Response;
  });
});
afterAll(() => vi.restoreAllMocks());

describe("adaptive Oberfläche", () => {
  it("startet im Arbeitsbereich, öffnet Ansichten passend und hält die Audio-Laufzeit stabil", async () => {
    const { mount } = await import("./app");
    const root = document.createElement("div");
    document.body.append(root);
    await act(async () => mount(root));
    await flush();

    const control = FakeSocket.all[0];
    expect(control.url).toContain("/control");
    await act(async () => {
      control.open();
      control.emit({ type: "WELCOME", role: "owner", snapshot: { status: "idle", metrics: {}, logs: [], transcripts: [] } });
    });
    await flush();
    expect(audio.instances).toHaveLength(1);
    expect(FakeSocket.all).toHaveLength(2); // Steuer- + Audio-Kanal

    // 1) Start: Arbeitsbereich mit Navigation, keine große Blase; Gespräch über die Navigation erreichbar
    expect(stage().dataset.view).toBe("conversation");
    expect(stage().classList.contains("workspace")).toBe(true);
    expect(document.querySelector(".view-frame")).toBeNull();
    expect(document.querySelector(".voice-core")).toBeNull();
    for (let i = 0; i < 200 && !document.querySelector(".cc-nav"); i++) await flush(); // Arbeitsbereich wird nachgeladen
    const nav = [...document.querySelectorAll(".cc-nav button")].map((b) => b.textContent?.trim());
    expect(nav).toEqual(expect.arrayContaining(["Heute", "Command Center", "Freigaben", "Agenten", "Aufträge", "Kalender", "Skills und Automationen"]));
    const center = [...document.querySelectorAll(".cc-nav button")].find((b) => b.textContent?.trim() === "Command Center") as HTMLButtonElement;
    await act(async () => center.click());
    expect(document.querySelector(".cc-center .cc-transport input")).not.toBeNull();
    expect(document.querySelector(".cc-center .cc-now")?.textContent).toContain("Bereit");

    // 2) Smalltalk bleibt im Gespräch
    await act(async () => user(control, "s0", "Wie geht es dir heute?"));
    expect(stage().dataset.view).toBe("conversation");

    // 3) Nachrichten öffnen die Welt, Blase wird Präsenzpunkt
    await act(async () => {
      user(control, "s1", "Zeig mir die Nachrichten");
      control.emit({ type: "TURN_START", stream_id: "s1" });
    });
    await flush();
    expect(stage().dataset.view).toBe("news");
    expect(document.querySelector('[data-test="news"]')).not.toBeNull();
    expect(document.querySelector(".voice-core.compact")).not.toBeNull();

    // 5) Zurück zum Gespräch
    await act(async () => user(control, "s2", "Zurück zum Gespräch"));
    await flush();
    expect(stage().dataset.view).toBe("conversation");
    expect(document.querySelector(".view-frame")).toBeNull();

    // 6) Abgebrochener Zug: verspätetes Werkzeug-/Transkript-Ereignis öffnet nichts
    await act(async () => {
      control.emit({ type: "TURN_START", stream_id: "s3" });
      control.emit({ type: "STREAM_STOPPED", stream_id: "s3", reason: "vad", ack_ms: 3 });
      control.emit({ type: "TOOL", stream_id: "s3", name: "nachrichten" });
      user(control, "s3", "Was gibt es Neues?");
    });
    await flush();
    expect(stage().dataset.view).toBe("conversation");

    // 4) Eindeutige Aktienanfrage -> Chartansicht mit dem richtigen Wertpapier
    await act(async () => user(control, "s4", "Wie hat sich Apple entwickelt?"));
    await flush();
    expect(stage().dataset.view).toBe("market");
    expect(document.querySelector('[data-test="market"]')?.textContent).toContain("apple");

    // 7) Kameras gezielt über das Menü; zweiter Monitor öffnet sich dabei nicht
    const open = vi.spyOn(window, "open").mockImplementation(() => null);
    await act(async () => (document.querySelector(".dock-menu") as HTMLButtonElement).click());
    const cams = [...document.querySelectorAll(".dock-popover button")].find((b) => b.textContent === "Kameras") as HTMLButtonElement;
    await act(async () => cams.click());
    await flush();
    expect(stage().dataset.view).toBe("cameras");
    expect(open).not.toHaveBeenCalled();
    // Manuelle Ansicht bleibt bei bloßem Werkzeug-Hinweis
    await act(async () => {
      control.emit({ type: "TURN_START", stream_id: "s5" });
      control.emit({ type: "TOOL", stream_id: "s5", name: "nachrichten" });
    });
    expect(stage().dataset.view).toBe("cameras");

    // Über alle Wechsel hinweg: keine neue Verbindung, keine neue AudioEngine, nichts geschlossen
    expect(FakeSocket.all).toHaveLength(2);
    expect(audio.instances).toHaveLength(1);
    expect(audio.instances[0].close).not.toHaveBeenCalled();
    expect(FakeSocket.all.every((socket) => socket.readyState === 1 || socket.readyState === 0)).toBe(true);
  });
});
