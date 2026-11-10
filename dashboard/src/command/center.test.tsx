// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CommandCenter, type CommandCenterProps } from "./CommandCenter";
import { Telemetry } from "./telemetry";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const fakeAudio = () => ({
  enableMic: vi.fn(async () => undefined),
  disableMic: vi.fn(),
  isRecording: () => false,
  isAudible: () => false,
  meter: () => ({ input: null, output: null, muted: false }),
  levels: () => ({ input: 0, output: 0 }),
});

let root: Root | null = null;
beforeEach(() => {
  // Mikrofon gibt es nur in sicheren Kontexten (localhost/https); happy-dom simuliert das nicht von selbst.
  Object.defineProperty(window, "isSecureContext", { value: true, configurable: true });
  Object.defineProperty(navigator, "mediaDevices", { value: { getUserMedia: vi.fn() }, configurable: true });
});
afterEach(() => {
  act(() => root?.unmount());
  root = null;
  document.body.innerHTML = "";
});

async function render(overrides: Partial<CommandCenterProps> = {}) {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  const props: CommandCenterProps = {
    telemetry: new Telemetry(), audio: fakeAudio() as any, role: "owner", connected: true, transcripts: [],
    micOn: false, onMicChange: vi.fn(), muted: false, onMutedChange: vi.fn(), onAsk: vi.fn(), onStop: vi.fn(), onLog: vi.fn(),
    automations: {
      stand: 0, ablaeufe: [], ablaeufe_schleife: "aus", integrationen: [], jobs_laufend: 0,
      n8n: { status: "nicht_eingerichtet", hinweis: "", workflows: [] },
      agenten: [{ id: "jarvis", name: "Jarvis", rolle: "", modell: "claude-haiku-5-5", status: "bereit", hinweis: "" }],
      sprache: { stimme: "George", stimme_anbieter: "ElevenLabs", stimme_modell: "eleven_flash_v2_5", stimme_hinweis: "", erkennung: "Whisper base · lokal" },
    },
    approvals: [], onOpenApprovals: vi.fn(), ...overrides,
  };
  await act(async () => root!.render(<CommandCenter {...props} />));
  return { host, props };
}
const key = (k: string, target: EventTarget = window) =>
  act(() => void target.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true })));

describe("Command Center", () => {
  it("zeigt nur echte Fakten und sieben Zustandssegmente", async () => {
    const { host } = await render();
    const facts = host.querySelector(".cc-hdr-facts")!.textContent!;
    expect(facts).toContain("claude-haiku-5-5");
    expect(facts).toContain("George · ElevenLabs");
    expect(facts).toContain("Whisper base · lokal");
    expect(host.querySelectorAll(".cc-seg")).toHaveLength(7);
    expect(host.querySelector(".cc-seg.active")!.textContent).toContain("Bereit");
    // Keine erfundenen Werte: ohne Zug stehen alle Kacheln auf „–“
    expect([...host.querySelectorAll(".cc-tile strong")].map((el) => el.textContent)).toEqual(["–", "–", "–", "–", "–"]);
  });

  it("ohne Server-Angaben bleibt die Kopfzeile ehrlich leer", async () => {
    const { host } = await render({ automations: null });
    expect(host.querySelector(".cc-hdr-facts")!.textContent).toContain("Stimme–");
  });

  it("Tastatur: S schaltet stumm, M wirkt nicht im Textfeld, Text wird gesendet", async () => {
    const { host, props } = await render();
    await key("s");
    expect(props.onMutedChange).toHaveBeenCalledWith(true);
    const input = host.querySelector<HTMLInputElement>(".cc-transport input")!;
    await key("/");
    expect(document.activeElement).toBe(input);
    await key("m", input);
    expect((props.audio as any).enableMic).not.toHaveBeenCalled();
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
      setter.call(input, "Was steht heute an?");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await act(async () => host.querySelector<HTMLFormElement>(".cc-transport")!.requestSubmit());
    expect(props.onAsk).toHaveBeenCalledWith("Was steht heute an?");
    await key("m");
    expect((props.audio as any).enableMic).toHaveBeenCalled();
  });

  it("erklärt, wenn das Mikrofon wegen unsicherer Adresse fehlt", async () => {
    Object.defineProperty(window, "isSecureContext", { value: false, configurable: true });
    const { host } = await render();
    await act(async () => host.querySelector<HTMLButtonElement>(".cc-transport .cc-tbtn")!.click());
    expect(host.querySelector(".cc-alert")!.textContent).toContain("localhost oder https");
  });

  it("erklärt ein blockiertes Mikrofon verständlich", async () => {
    const audio = fakeAudio();
    audio.enableMic = vi.fn(async () => { throw Object.assign(new Error("denied"), { name: "NotAllowedError" }); });
    const { host } = await render({ audio: audio as any });
    await act(async () => host.querySelector<HTMLButtonElement>(".cc-transport .cc-tbtn")!.click());
    expect(host.querySelector(".cc-alert")!.textContent).toContain("Mikrofon ist blockiert");
  });

  it("zeigt Offline mit Vorrang und einen Fehler mit Stufe", async () => {
    const telemetry = new Telemetry();
    const { host } = await render({ telemetry, connected: false });
    expect(host.querySelector(".cc-seg.active")!.textContent).toContain("Offline");
    await act(async () => root!.render(<CommandCenter {...({} as any)} {...{
      telemetry, audio: fakeAudio() as any, role: "owner", connected: true, transcripts: [], micOn: false, onMicChange: vi.fn(),
      muted: false, onMutedChange: vi.fn(), onAsk: vi.fn(), onStop: vi.fn(), onLog: vi.fn(), automations: null, approvals: [], onOpenApprovals: vi.fn(),
    }} />));
    await act(async () => telemetry.onServer({ type: "ERROR", stage: "stt", message: "Spracherkennung ist belegt." }));
    expect(host.querySelector(".cc-seg.active")!.textContent).toContain("Fehler");
    expect(host.querySelector('[role="alert"]')!.textContent).toContain("Erkennung: Spracherkennung ist belegt.");
  });
});
