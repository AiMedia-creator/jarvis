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
const openDiag = (host: HTMLElement) =>
  act(async () => [...host.querySelectorAll<HTMLButtonElement>(".cc-bar button")].find((b) => b.textContent?.startsWith("Diagnose"))!.click());
const key = (k: string, target: EventTarget = window) =>
  act(() => void target.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true })));

describe("Command Center", () => {
  it("zeigt im Normalfall Gespräch und Vorgänge, die Messung erst in der Diagnose", async () => {
    const { host } = await render();
    expect(host.querySelector(".cc-now")!.textContent).toContain("Bereit");
    expect(host.querySelector(".cc-thread")!.textContent).toContain("Was soll Jarvis erledigen?");
    expect(host.querySelector(".cc-hdr-facts")).toBeNull();
    expect(host.querySelector(".cc-tile")).toBeNull();
    await openDiag(host);
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
    await openDiag(host);
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
    expect(host.querySelector(".cc-now")!.textContent).toContain("Offline");
    await act(async () => root!.render(<CommandCenter {...({} as any)} {...{
      telemetry, audio: fakeAudio() as any, role: "owner", connected: true, transcripts: [], micOn: false, onMicChange: vi.fn(),
      muted: false, onMutedChange: vi.fn(), onAsk: vi.fn(), onStop: vi.fn(), onLog: vi.fn(), automations: null, approvals: [], onOpenApprovals: vi.fn(),
    }} />));
    await act(async () => telemetry.onServer({ type: "ERROR", stage: "stt", message: "Spracherkennung ist belegt." }));
    expect(host.querySelector(".cc-now")!.textContent).toContain("Fehler");
    expect(host.querySelector('[role="alert"]')!.textContent).toContain("Erkennung: Spracherkennung ist belegt.");
  });

  it("zeigt das ganze Gespräch und füllt Einstiege nur ins Feld", async () => {
    const { host, props } = await render();
    const starter = host.querySelector<HTMLButtonElement>(".cc-thread-empty li button")!;
    await act(async () => starter.click());
    expect(host.querySelector<HTMLInputElement>(".cc-transport input")!.value).toContain("Was steht heute an?");
    expect(props.onAsk).not.toHaveBeenCalled();
    await act(async () => root!.render(<CommandCenter {...props} transcripts={[
      { id: "u1", role: "user", text: "Leg einen Auftrag für Weber an.", stream_id: "s1", final: true },
      { id: "a1", role: "assistant", text: "Erledigt – Auftrag steht im Eingang.", stream_id: "s1", final: true },
      { id: "u2", role: "user", text: "Danke.", stream_id: "s2", final: true },
    ]} />));
    expect([...host.querySelectorAll(".cc-thread .cc-line")].map((el) => el.textContent)).toEqual([
      "DuLeg einen Auftrag für Weber an.", "JarvisErledigt – Auftrag steht im Eingang.", "DuDanke.",
    ]);
  });

  it("zeigt laufende Vorgänge aus echten Jobs und bricht nur im steuernden Fenster ab", async () => {
    const job = { id: "j1", titel: "Repo analysieren", art: "faehigkeit", status: "RUNNING", agent: { id: "jarvis", name: "Jarvis", art: "haupt" },
      unteragenten: [], gespraech: "", start: Date.now() / 1000 - 30, ende: null, letzte_aktivitaet: 0, schritte: [], schritte_gesamt: 3, schritte_fertig: 1,
      fortschritt: "", werkzeuge: [], ergebnis: null, fehler: null, unterbrochen_durch_neustart: false, freigaben: [], abbrechbar: true, wiederholbar: false,
      faehigkeit: null, modell: null, nutzung: null, repo: null };
    const onCancelJob = vi.fn(), onOpenJob = vi.fn();
    const { host, props } = await render({ jobs: [job, { ...job, id: "j0", status: "COMPLETED", titel: "Alt" }] as any, onCancelJob, onOpenJob });
    const ops = host.querySelector(".cc-ops")!;
    expect(ops.textContent).toContain("Repo analysieren");
    expect(ops.textContent).toContain("Schritt 1/3");
    expect(ops.textContent).not.toContain("Alt");
    await act(async () => ops.querySelector<HTMLButtonElement>(".cc-op")!.click());
    expect(onOpenJob).toHaveBeenCalledWith("j1");
    await act(async () => [...ops.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent === "Abbrechen")!.click());
    expect(onCancelJob).toHaveBeenCalledWith("j1");
    await act(async () => root!.render(<CommandCenter {...props} role="observer" />));
    expect([...host.querySelectorAll(".cc-ops button")].some((b) => b.textContent === "Abbrechen")).toBe(false);
  });

  it("nennt offene Freigaben und führt zur Entscheidung", async () => {
    const onOpenApprovals = vi.fn();
    const { host } = await render({ onOpenApprovals, approvals: [{ id: "f1", werkzeug: "mail_senden", status: "WAITING_FOR_APPROVAL", status_text: "", ergebnis: "",
      erstellt: 0, laeuft_ab: Date.now() / 1000 + 600, job: null, graph: null, vorschau: { titel: "E-Mail an Weber", ziel: "Gmail", zeilen: [] } }] });
    const box = host.querySelector(".cc-center-side section.attn")!;
    expect(box.textContent).toContain("E-Mail an Weber");
    await act(async () => box.querySelector<HTMLButtonElement>("button")!.click());
    expect(onOpenApprovals).toHaveBeenCalled();
  });
});
