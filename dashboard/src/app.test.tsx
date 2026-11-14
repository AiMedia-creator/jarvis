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

const waitFor = async (selector: string) => { for (let i = 0; i < 200 && !document.querySelector(selector); i++) await flush(); };
const welcome = async (control: FakeSocket) => {
  await act(async () => {
    control.open();
    control.emit({ type: "WELCOME", role: "owner", snapshot: { status: "idle", metrics: {}, logs: [], transcripts: [] } });
  });
  await flush();
};

describe("Dashboard-Fenster", () => {
  it("hat eine Schiene für alles, öffnet „Mehr“-Ansichten darin und hält weder Audio-Kanal noch Mikrofon", async () => {
    const { mount } = await import("./app");
    const root = document.createElement("div");
    document.body.append(root);
    await act(async () => mount(root));
    await flush();

    const control = FakeSocket.all[0];
    expect(control.url).toContain("/control");
    await welcome(control);
    // Sprache liegt im Command-Center-Fenster: das Dashboard baut keine AudioEngine und keinen Audio-Kanal auf.
    expect(audio.instances).toHaveLength(0);
    expect(FakeSocket.all).toHaveLength(1);

    // 1) Start: Arbeitsbereich mit Navigation; das Command Center ist ein Link, kein Abschnitt
    expect(stage().dataset.view).toBe("conversation");
    expect(stage().classList.contains("workspace")).toBe(true);
    await waitFor(".cc-nav");
    const nav = [...document.querySelectorAll(".cc-nav button")].map((b) => b.textContent?.trim());
    expect(nav).toEqual(expect.arrayContaining(["Heute", "Freigaben", "Agenten / Jobs", "CRM", "Aufträge", "Kalender", "Skills und Automationen"]));
    expect(nav).not.toContain("Command Center");
    // Eingeklappt ist nur die Marke (Punkt + „Jarvis“) Teil der sichtbaren Schiene; Maus, Tastatur und Touch klappen aus
    const rail = document.querySelector<HTMLElement>(".cc-nav")!;
    const brand = rail.querySelector<HTMLButtonElement>("button.cc-brand")!;
    const body = document.getElementById(brand.getAttribute("aria-controls")!)!;
    expect(brand.textContent).toBe("Jarvis");
    expect(brand.querySelector(".cc-logo")).not.toBeNull();
    expect([...rail.children]).toEqual([brand, body]); // alles Übrige liegt im einklappbaren Teil
    expect(rail.classList.contains("open")).toBe(false);
    expect(brand.getAttribute("aria-expanded")).toBe("false");
    const pointer = (type: string, pointerType: string, target: Element = rail) => act(async () => {
      const event = new Event(type, { bubbles: type !== "pointerenter" && type !== "pointerleave" });
      Object.defineProperty(event, "pointerType", { value: pointerType });
      target.dispatchEvent(event);
    });
    // React hört für pointerenter/-leave auf pointerover/-out
    await pointer("pointerover", "mouse");
    expect(rail.classList.contains("open")).toBe(true);
    expect(brand.getAttribute("aria-expanded")).toBe("true");
    await act(async () => {
      const out = new Event("pointerout", { bubbles: true });
      Object.defineProperty(out, "pointerType", { value: "mouse" });
      Object.defineProperty(out, "relatedTarget", { value: document.body });
      rail.dispatchEvent(out);
    });
    expect(rail.classList.contains("open")).toBe(false);
    // Touch: Antippen der Marke schaltet um (kein Hover nötig); ein Tipp daneben oder Esc schließt
    await pointer("pointerdown", "touch", brand);
    await act(async () => brand.click());
    expect(rail.classList.contains("open")).toBe(true);
    expect(brand.getAttribute("aria-label")).toBe("Jarvis – Navigation einklappen");
    await act(async () => document.querySelector<HTMLButtonElement>(".cc-backdrop")!.click());
    expect(rail.classList.contains("open")).toBe(false);
    await act(async () => brand.click()); // Tastatur (Enter/Leertaste löst click ohne Zeiger aus)
    expect(brand.getAttribute("aria-expanded")).toBe("true");
    await act(async () => { rail.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })); });
    expect(brand.getAttribute("aria-expanded")).toBe("false");
    // Mausklick auf die Marke heftet nichts an: das Überfahren regelt Öffnen und Schließen
    await pointer("pointerdown", "mouse", brand);
    await act(async () => brand.click());
    expect(rail.classList.contains("pinned")).toBe(false);
    const link = document.querySelector<HTMLAnchorElement>(".cc-nav a.cc-open-cc")!;
    expect(link.getAttribute("href")).toBe("?screen=command");
    const open = vi.spyOn(window, "open").mockImplementation(() => ({ location: { href: "about:blank", search: "" }, focus() {} }) as unknown as Window);
    await act(async () => link.click());
    expect(open).toHaveBeenCalledWith("", "jarvis-command");
    expect(document.querySelector(".cc-center")).toBeNull();
    open.mockClear();

    // 2) Smalltalk ändert nichts
    await act(async () => user(control, "s0", "Wie geht es dir heute?"));
    expect(stage().dataset.view).toBe("conversation");

    // 3) Nachrichten öffnen sich in derselben Schiene (keine eigene Bühne, kein Sprach-Dock)
    await act(async () => {
      user(control, "s1", "Zeig mir die Nachrichten");
      control.emit({ type: "TURN_START", stream_id: "s1" });
    });
    await flush();
    expect(stage().dataset.view).toBe("news");
    await waitFor('[data-test="news"]');
    expect(document.querySelector('.cc-work.lt-stage [data-test="news"]')).not.toBeNull();
    expect(document.querySelector(".cc-nav")).not.toBeNull();
    expect(document.querySelector(".voice-core")).toBeNull();
    expect(document.querySelector(".dock")).toBeNull();

    // 4) Zurück
    await act(async () => user(control, "s2", "Zurück zum Gespräch"));
    await flush();
    expect(stage().dataset.view).toBe("conversation");
    expect(document.querySelector(".view-frame")).toBeNull();

    // 5) Abgebrochener Zug: verspätetes Werkzeug-/Transkript-Ereignis öffnet nichts
    await act(async () => {
      control.emit({ type: "TURN_START", stream_id: "s3" });
      control.emit({ type: "STREAM_STOPPED", stream_id: "s3", reason: "vad", ack_ms: 3 });
      control.emit({ type: "TOOL", stream_id: "s3", name: "nachrichten" });
      user(control, "s3", "Was gibt es Neues?");
    });
    await flush();
    expect(stage().dataset.view).toBe("conversation");

    // 6) Eindeutige Aktienanfrage -> Chartansicht mit dem richtigen Wertpapier
    await act(async () => user(control, "s4", "Wie hat sich Apple entwickelt?"));
    await flush();
    expect(stage().dataset.view).toBe("market");
    await waitFor('[data-test="market"]');
    expect(document.querySelector('[data-test="market"]')?.textContent).toContain("apple");

    // 7) Kameras über „Mehr“ in der Schiene; kein zweites Fenster
    const cams = [...document.querySelectorAll(".cc-nav button")].find((b) => b.textContent?.trim() === "Kameras") as HTMLButtonElement;
    await act(async () => cams.click());
    await flush();
    expect(stage().dataset.view).toBe("cameras");
    expect(cams.getAttribute("aria-current")).toBe("page");
    expect(open).not.toHaveBeenCalled();
    // Ein Abschnitt aus der Schiene schließt die „Mehr“-Ansicht
    const today = [...document.querySelectorAll(".cc-nav button")].find((b) => b.textContent?.trim() === "Heute") as HTMLButtonElement;
    await act(async () => today.click());
    await flush();
    expect(stage().dataset.view).toBe("conversation");

    // Über alle Wechsel hinweg: keine neue Verbindung, nichts geschlossen
    expect(FakeSocket.all).toHaveLength(1);
    expect(FakeSocket.all.every((socket) => socket.readyState === 1 || socket.readyState === 0)).toBe(true);
    open.mockRestore();
    await act(async () => root.remove());
  });
});

describe("Command-Center-Fenster (?screen=command)", () => {
  it("steuert in einem eigenen Raum, zeigt den Sprachkreis und öffnet bei „Zeig Berlin“ die Karte", async () => {
    vi.resetModules();
    FakeSocket.all.length = 0;
    audio.instances.length = 0;
    document.body.innerHTML = "";
    window.history.replaceState(null, "", "/?screen=command");
    vi.doMock("./world/MapboxWorld", () => ({ default: ({ query }: { query?: string }) => <div data-test="map">KARTE {query ?? ""}</div> }));
    localStorage.setItem("jarvis-command-pending-v1", JSON.stringify({ id: "p1", text: "Briefing bitte", ts: Date.now() }));
    const { mount } = await import("./app");
    const root = document.createElement("div");
    document.body.append(root);
    await act(async () => mount(root));
    await flush();

    const control = FakeSocket.all[0];
    expect(control.url).toContain("screen=main"); // eigener Raum, dort Hauptfenster = Besitzer
    expect(localStorage.getItem("jarvis-room-command")).toBeTruthy();
    expect(control.url).toContain(localStorage.getItem("jarvis-room-command")!);
    await welcome(control);
    expect(audio.instances).toHaveLength(1);
    expect(FakeSocket.all).toHaveLength(2); // Steuer- + Audio-Kanal

    await waitFor(".cc.cw .orb");
    expect(document.querySelector(".cc-nav")).toBeNull(); // eigenes Fenster, nicht das Dashboard
    expect(document.querySelector(".orb-wrap")!.getAttribute("data-state")).toBe("bereit");
    expect(document.querySelector(".orb-mic")!.textContent).toBe("Mikrofon aus"); // kein offenes Mikrofon behauptet
    expect(document.querySelector(".cc.cw")!.textContent).toContain("Master");
    expect(document.querySelector(".cc-transport input")).not.toBeNull(); // Texteingabe als gleichwertiger Weg
    // Der vom Dashboard übergebene Auftrag wurde genau einmal gesendet
    const sent = control.sent.map((x) => JSON.parse(x)).filter((m) => m.type === "USER_TEXT");
    expect(sent).toEqual([expect.objectContaining({ text: "Briefing bitte" })]);
    expect(localStorage.getItem("jarvis-command-pending-v1")).toBeNull();

    await act(async () => user(control, "b1", "Zeig Berlin"));
    await waitFor('[data-test="map"]');
    expect(document.querySelector(".cw-map h2")!.textContent).toBe("Berlin");
    expect(document.querySelector('[data-test="map"]')!.textContent).toContain("Berlin");
    expect(document.querySelector(".cc-understood")!.textContent).toContain("Karte öffnen · Berlin");

    // Andere Ansichten gehören ins Dashboard: das Command Center bleibt beim Gespräch und sagt, wohin es ging
    await act(async () => user(control, "b2", "Zeig mir die Nachrichten"));
    await flush();
    expect(document.querySelector('[data-test="news"]')).toBeNull();
    expect(document.querySelector(".cc.cw")!.textContent).toContain("Nachrichten im Dashboard-Fenster geöffnet");

    expect(FakeSocket.all).toHaveLength(2);
    expect(audio.instances).toHaveLength(1);
    expect(audio.instances[0].close).not.toHaveBeenCalled();
    window.history.replaceState(null, "", "/");
  });
});
