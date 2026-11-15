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
    await act(async () => [...host.querySelectorAll<HTMLButtonElement>(".cc-bar button")].find((b) => b.textContent?.startsWith("Diagnose"))!.click());
    expect(host.querySelector(".cc-chain.realtime")!.textContent).toContain("ElevenLabs Agents");
    expect(host.querySelector(".cc-transport")!.textContent).toContain("Echtzeit beenden");
    expect(host.querySelector(".cc-transport")!.textContent).not.toContain("Abbrechen");
    act(() => root.unmount());
  });

  it("lässt den Verbindungsaufbau abbrechen und behauptet dabei kein offenes Mikrofon", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const telemetry = new Telemetry();
    telemetry.setConnected(true);
    telemetry.realtimeState({ mode: "verbindet", toolRunning: false });
    const stop = vi.fn(async () => undefined);
    await act(async () => root.render(<CommandCenter {...base} telemetry={telemetry} stage voiceMode="echtzeit" onStopRealtime={stop} />));
    expect(host.querySelector(".cc-now")!.textContent).toContain("Verbindet");
    expect(host.querySelector(".orb-text")!.textContent).toContain("Mikrofon aus");
    expect(host.querySelector(".orb-text")!.textContent).not.toContain("Mikrofon aktiv");
    const cancel = [...host.querySelectorAll<HTMLButtonElement>(".cc-transport button")].find((b) => b.textContent?.includes("Verbinden abbrechen"))!;
    expect(cancel.disabled).toBe(false);
    await act(async () => cancel.click());
    expect(stop).toHaveBeenCalledTimes(1);
    // Sitzung steht: jetzt ist das Mikrofon wirklich offen.
    await act(async () => telemetry.realtimeMode("listening"));
    expect(host.querySelector(".orb-text")!.textContent).toContain("Mikrofon aktiv");
    expect(host.querySelector(".cc-transport")!.textContent).toContain("Echtzeit beenden");
    act(() => root.unmount());
  });

  it("zeigt bleibend an, warum Echtzeit nicht gestartet ist", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const start = vi.fn(async () => "Echtzeit nicht gestartet: Mikrofon ist blockiert.");
    await act(async () => root.render(<CommandCenter {...base} settings={{ ...settings, sprachkern_verfuegbar: true }} onStartRealtime={start} />));
    await act(async () => [...host.querySelectorAll<HTMLButtonElement>(".cc-mode button")].find((b) => b.textContent === "Echtzeit")!.click());
    expect(host.querySelector(".cc-alert.soft")!.textContent).toContain("Mikrofon ist blockiert");
    act(() => root.unmount());
  });

  it("sagt, warum eine Äußerung nicht beantwortet wurde, und zeigt den Türsteher in der Diagnose", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const tuersteher = { an: true, aktiv: true, modell: "jev-1.14.0", problem: null };
    await act(async () => root.render(<CommandCenter {...base} stage settings={{ ...settings, tuersteher }}
      gateNote={{ aktion: "ignorieren", grund: "nicht an Jarvis", t: Date.now() }} />));
    expect(host.querySelector(".cc-gate")!.textContent).toContain("klang nicht an Jarvis gerichtet");
    await act(async () => [...host.querySelectorAll<HTMLButtonElement>(".cc-bar button")].find((b) => b.textContent?.startsWith("Diagnose"))!.click());
    expect(host.querySelector(".cc-diag")!.textContent).toContain("TürsteherTypeSafe · jev-1.14.0");
    act(() => root.unmount());
  });

  it("schaltet den Türsteher in den Einstellungen um und nennt den fehlenden Schlüssel", async () => {
    const saved: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init: RequestInit) => {
      saved.push(JSON.parse(String(init.body)));
      return new Response(JSON.stringify({ ...settings, tuersteher: { an: false, aktiv: false, modell: "jev-1.14.0", problem: null } }), { status: 200 });
    }));
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const onSettings = vi.fn();
    await act(async () => root.render(<CommandCenter {...base} onSettings={onSettings}
      settings={{ ...settings, tuersteher: { an: true, aktiv: false, modell: "jev-1.14.0", problem: "TYPESAFE_API_KEY fehlt" } }} />));
    await act(async () => [...host.querySelectorAll<HTMLButtonElement>(".cc-bar button")].find((b) => b.textContent === "Einstellungen")!.click());
    const dialog = host.querySelector(".cc-dialog")!;
    expect(dialog.textContent).toContain("Nicht aktiv: TYPESAFE_API_KEY fehlt");
    const box = [...dialog.querySelectorAll<HTMLInputElement>("input[type=checkbox]")].find((i) => i.closest("label")?.textContent?.includes("Sprach-Türsteher"))!;
    await act(async () => box.click());
    expect(saved).toEqual([{ tuersteher: false }]);
    expect(onSettings).toHaveBeenCalled();
    act(() => root.unmount());
  });

  it("öffnet die Einstellungen als Dialog", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    await act(async () => root.render(<CommandCenter {...base} settings={settings} />));
    const button = [...host.querySelectorAll<HTMLButtonElement>(".cc-bar button")].find((b) => b.textContent === "Einstellungen")!;
    await act(async () => button.click());
    const dialog = host.querySelector('[role="dialog"]')!;
    expect(dialog.textContent).toContain("Sprechpause");
    expect(dialog.textContent).toContain("Push-to-talk");
    act(() => root.unmount());
  });

  it("zeigt alle Anbieter und sperrt nicht eingerichtete mit Begründung", async () => {
    const host = document.createElement("div");
    document.body.append(host);
    const root = createRoot(host);
    const choices = [
      { key: "opus", anbieter: "anthropic", anbieter_name: "Anthropic (Claude)", modell: "claude-opus-5-5", name: "Claude Opus 5.5", verfuegbar: true, problem: null, hinweis: "" },
      { key: "sonnet", anbieter: "anthropic", anbieter_name: "Anthropic (Claude)", modell: "claude-sonnet-5-5", name: "Claude Sonnet 5.5", verfuegbar: true, problem: null, hinweis: "" },
      { key: "kimi", anbieter: "moonshot", anbieter_name: "Moonshot (Kimi)", modell: "kimi-k3.1-pro", name: "Kimi K3.1 Pro", verfuegbar: false, problem: "MOONSHOT_API_KEY fehlt.", hinweis: "" },
      { key: "ollama", anbieter: "ollama", anbieter_name: "Ollama (lokal)", modell: "", name: "Ollama: kein Modell gewählt", verfuegbar: false, problem: "Kein Ollama-Modell gewählt", hinweis: "" },
    ];
    await act(async () => root.render(<CommandCenter {...base} settings={{ ...settings, modell_key: "opus", modell_auswahl: choices as any, anbieter: "Anthropic (Claude)" }} />));
    await act(async () => [...host.querySelectorAll<HTMLButtonElement>(".cc-bar button")].find((b) => b.textContent === "Einstellungen")!.click());
    const buttons = [...host.querySelectorAll<HTMLButtonElement>(".cc-models button")];
    expect(buttons.map((b) => b.querySelector("strong")!.textContent)).toEqual(["Claude Opus 5.5", "Claude Sonnet 5.5", "Kimi K3.1 Pro", "Ollama: kein Modell gewählt"]);
    expect(buttons[0].getAttribute("aria-checked")).toBe("true");
    expect(buttons[2].disabled).toBe(true);
    expect(buttons[2].textContent).toContain("MOONSHOT_API_KEY fehlt.");
    expect([...host.querySelectorAll<HTMLOptionElement>("option")].map((o) => o.textContent)).toContain("Claude Sonnet 5.5");
    // Backup: nur verfügbare Modelle desselben Anbieters, nicht das Hauptmodell selbst
    const backup = host.querySelector<HTMLSelectElement>(".cc-form select:last-of-type")!;
    const options = [...host.querySelectorAll<HTMLOptionElement>("option")].map((o) => o.textContent);
    expect(options).toContain("Kein Backup");
    expect(options).not.toContain("Kimi K3.1 Pro");
    expect(backup).toBeTruthy();
    act(() => root.unmount());
  });
});
