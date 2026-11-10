// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cameraSource } from "./LiveCamera";
import { CommandCenter } from "./CommandCenter";
import { loadPrefs, savePrefs } from "./SettingsDialog";
import { Telemetry } from "./telemetry";
import { tracksFor } from "./TurnTape";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
afterEach(() => { document.body.innerHTML = ""; vi.unstubAllGlobals(); });

describe("Live-Kameras", () => {
  it("bettet nur YouTube-IDs und Windy-Adressen ein", () => {
    expect(cameraSource({ art: "youtube", video: "dQw4w9WgXcQ" })?.src).toContain("youtube-nocookie.com/embed/dQw4w9WgXcQ");
    expect(cameraSource({ art: "youtube", video: "x\"><script>" })).toBeNull();
    expect(cameraSource({ art: "windy", url: "https://webcams.windy.com/webcams/public/embed/player/1" })?.kind).toBe("iframe");
    expect(cameraSource({ art: "windy", url: "https://evil.example/windy.com/" })).toBeNull();
    expect(cameraSource({ art: "bild", url: "https://images-webcams.windy.com/1.jpg" })?.kind).toBe("img");
  });
});

describe("Echtzeit-Züge", () => {
  it("zeigen Zustände des Anbieters und keine erfundenen Stufenzeiten", () => {
    const t = new Telemetry();
    t.realtimeState({ mode: "listening", toolRunning: false });
    expect(t.derive({ connected: true, recording: false, audible: false, now: 1 }).active).toBe("hoert");
    t.realtimeUser("Wie wird das Wetter?", 100);
    t.realtimeTool("wetter", true, 150);
    expect(t.derive({ connected: true, recording: false, audible: false, now: 160 }).active).toBe("denkt");
    t.realtimeTool("wetter", false, 400);
    t.realtimeMode("speaking", 700);
    expect(t.derive({ connected: true, recording: false, audible: false, now: 710 }).active).toBe("spricht");
    t.realtimeAgent("Sonnig.");
    t.realtimeMode("listening", 1500);
    const turn = t.turns[0];
    expect(turn.source).toBe("realtime");
    expect(turn.texts).toEqual([{ role: "user", text: "Wie wird das Wetter?" }, { role: "assistant", text: "Sonnig." }]);
    expect(Object.values(t.stages(turn)).every((v) => v === null)).toBe(true);
    const { aligned, tracks } = tracksFor(t, turn, 1600);
    expect(aligned).toBe(false);
    expect(tracks.find((x) => x.key === "llm")!.marks).toHaveLength(1);
    expect(tracks.find((x) => x.key === "tts")!.blocks.some((b) => b.from === 700 && b.to === 1500)).toBe(true);
  });
});

describe("Browser-Einstellungen", () => {
  it("werden begrenzt geladen und gespeichert", () => {
    const store = new Map<string, string>();
    vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) });
    savePrefs({ pause: 9, barge: "aus", talkMode: "ptt" });
    expect(loadPrefs()).toEqual({ pause: 2.5, barge: "aus", talkMode: "ptt" });
    localStorage.setItem("jarvis.cc.voice", "kaputt");
    expect(loadPrefs().pause).toBe(1.2);
  });
});

describe("Umschalter Studio | Echtzeit", () => {
  const base = {
    telemetry: new Telemetry(), audio: null, role: "owner" as const, connected: true, transcripts: [], micOn: false, onMicChange: vi.fn(),
    muted: false, onMutedChange: vi.fn(), onAsk: vi.fn(), onStop: vi.fn(), onLog: vi.fn(), automations: null, approvals: [], onOpenApprovals: vi.fn(),
  };
  const settings = { modell: "claude-haiku-5-5", modell_key: "haiku", modelle: ["haiku", "sonnet", "opus"], stimme: "x", tempo: 1, stimmen: [],
    elevenlabs: true, elevenlabs_problem: null, sprachkern_verfuegbar: false, sprachkern_problem: "ELEVENLABS_API_KEY fehlt",
    eleven_tts_model: "eleven_flash_v2_5", eleven_tts_modelle: [], google: false, hubspot: false };

  it("sperrt Echtzeit mit Begründung, wenn ElevenLabs fehlt", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const start = vi.fn(async () => undefined);
    await act(async () => root.render(<CommandCenter {...base} settings={settings} onStartRealtime={start} />));
    const echtzeit = [...host.querySelectorAll<HTMLButtonElement>(".cc-mode button")].find((b) => b.textContent === "Echtzeit")!;
    expect(echtzeit.disabled).toBe(true);
    expect(echtzeit.title).toContain("ELEVENLABS_API_KEY fehlt");
    act(() => root.unmount());
  });

  it("startet Echtzeit und zeigt die Kette als eine Anbieter-Stufe", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const start = vi.fn(async () => undefined);
    await act(async () => root.render(<CommandCenter {...base} settings={{ ...settings, sprachkern_verfuegbar: true }} onStartRealtime={start} />));
    const echtzeit = [...host.querySelectorAll<HTMLButtonElement>(".cc-mode button")].find((b) => b.textContent === "Echtzeit")!;
    await act(async () => echtzeit.click());
    expect(start).toHaveBeenCalledTimes(1);
    await act(async () => root.render(<CommandCenter {...base} settings={{ ...settings, sprachkern_verfuegbar: true }} voiceMode="echtzeit" />));
    expect(host.querySelector(".cc-chain.realtime")!.textContent).toContain("ElevenLabs Agents");
    expect(host.querySelector(".cc-transport")!.textContent).toContain("Echtzeit beenden");
    expect(host.querySelector(".cc-transport")!.textContent).not.toContain("Abbrechen");
    act(() => root.unmount());
  });

  it("öffnet die Einstellungen als Dialog", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<CommandCenter {...base} settings={settings} />));
    const button = [...host.querySelectorAll<HTMLButtonElement>(".cc-transport button")].find((b) => b.textContent === "Einstellungen")!;
    await act(async () => button.click());
    const dialog = host.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).toContain("Sprechpause");
    expect(dialog.textContent).toContain("Push-to-talk");
    act(() => root.unmount());
  });
});
