import { describe, it, expect, vi } from "vitest";
import { AudioEngine } from "./audio";

describe("continuous microphone PCM contract", () => {
  it("sends every 20ms frame exactly once through arbitrary worklet boundaries", () => {
    const sent: unknown[] = [];
    const stopControl = vi.fn();
    const engine = new AudioEngine({
      state: vi.fn(),
      log: vi.fn(),
      stopControl,
    });
    const internal = engine as any;
    internal.ctx = { sampleRate: 48000 };
    vi.stubGlobal("WebSocket", { OPEN: 1 });
    engine.attach({
      readyState: 1,
      bufferedAmount: 0,
      send: (packet: unknown) => sent.push(packet),
    } as unknown as WebSocket);
    for (let i = 0; i < 100; i++)
      internal.accept(new Float32Array(960).fill(0.2));
    const binary = sent.filter(
      (item) => item instanceof ArrayBuffer,
    ) as ArrayBuffer[];
    expect(binary.length).toBe(100);
    expect(binary.every((packet) => packet.byteLength === 640)).toBe(true);
    expect(stopControl).toHaveBeenCalledExactlyOnceWith("vad");
    expect(sent.filter((item) => typeof item === "string").length).toBe(1);
    expect(
      JSON.parse(sent.find((item) => typeof item === "string") as string).type,
    ).toBe("AUDIO_START");
    vi.unstubAllGlobals();
  });
});

describe("bounded playback gap recovery", () => {
  it("decodes packet 2 after a missing packet 1 without a new turn", async () => {
    vi.useFakeTimers();
    const decode = vi.fn(async () => ({ duration: 0.1 }));
    const engine = new AudioEngine({
      state: vi.fn(),
      log: vi.fn(),
      stopControl: vi.fn(),
    });
    const inner = engine as any;
    inner.ctx = {
      decodeAudioData: decode,
      currentTime: 0,
      destination: {},
      createBufferSource: () => ({
        connect: vi.fn(),
        start: vi.fn(),
        stop: vi.fn(),
      }),
    };
    engine.turnStarted("gap-test-stream");
    inner.acceptPacket("gap-test-stream", 0, new Uint8Array([1]));
    await inner.decodeChain;
    inner.acceptPacket("gap-test-stream", 2, new Uint8Array([2]));
    await inner.decodeChain;
    await vi.advanceTimersByTimeAsync(130);
    await inner.decodeChain;
    expect(decode).toHaveBeenCalledTimes(2);
    expect(inner.pending.size).toBe(0);
    engine.stopPlayback();
    vi.useRealTimers();
  });
});

describe("Mikrofon-Start", () => {
  it("lässt nach einem Fehler kein offenes Mikrofon zurück und teilt doppelte Starts", async () => {
    const stop = vi.fn();
    const getUserMedia = vi.fn(async () => ({ getTracks: () => [{ stop }] }));
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
    vi.stubGlobal("AudioWorkletNode", class { port = {}; disconnect() {} });
    const engine = new AudioEngine({ state: vi.fn(), log: vi.fn(), stopControl: vi.fn() });
    let fail = true;
    (engine as any).ctx = {
      state: "running",
      destination: {},
      audioWorklet: { addModule: vi.fn(async () => { if (fail) throw new Error("Worklet nicht ladbar"); }) },
      createMediaStreamSource: () => ({ connect: () => ({ connect: () => ({ connect: vi.fn() }) }) }),
      createGain: () => ({ gain: { value: 1 } }),
    };
    await expect(engine.enableMic()).rejects.toThrow("Worklet nicht ladbar");
    expect(stop).toHaveBeenCalledTimes(1); // Spur beendet: der Browser zeigt kein offenes Mikrofon
    expect(engine.micActive()).toBe(false);
    fail = false;
    await Promise.all([engine.enableMic(), engine.enableMic()]); // Doppelklick
    expect(getUserMedia).toHaveBeenCalledTimes(2); // ein Fehlversuch, danach genau ein gemeinsamer Start
    expect(engine.micActive()).toBe(true);
    vi.unstubAllGlobals();
  });
});

describe("Vorläufiges Reinreden (Sprach-Türsteher)", () => {
  function setup(audible: boolean, gate = true) {
    vi.useFakeTimers();
    const sent: string[] = [];
    const stopControl = vi.fn();
    const engine = new AudioEngine({ state: vi.fn(), log: vi.fn(), stopControl });
    const inner = engine as any;
    inner.ctx = { sampleRate: 48000, currentTime: 0 };
    inner.muteGain = { gain: { setTargetAtTime: vi.fn(), value: 1 } };
    vi.spyOn(engine, "isAudible").mockReturnValue(audible);
    engine.setTentativeBarge(gate);
    const stopPlayback = vi.spyOn(engine, "stopPlayback");
    vi.stubGlobal("WebSocket", { OPEN: 1 });
    engine.attach({ readyState: 1, bufferedAmount: 0, send: (p: unknown) => { if (typeof p === "string") sent.push(p); } } as unknown as WebSocket);
    inner.beginRecording(performance.now());
    const start = JSON.parse(sent[0]);
    return { engine, inner, stopControl, stopPlayback, start };
  }

  it("stellt Jarvis nur leiser, solange der Server prüft, und stoppt ihn nicht", () => {
    const { engine, inner, stopControl, stopPlayback, start } = setup(true);
    expect(start).toMatchObject({ type: "AUDIO_START", tentative: true });
    expect(stopControl).not.toHaveBeenCalled();
    expect(stopPlayback).not.toHaveBeenCalled();
    expect(engine.isDucked()).toBe(true);
    expect(inner.muteGain.gain.setTargetAtTime).toHaveBeenLastCalledWith(0.25, 0, 0.03);
    engine.bargeIgnored(start.utterance_id);   // nicht an Jarvis gerichtet
    expect(engine.isDucked()).toBe(false);
    expect(inner.muteGain.gain.setTargetAtTime).toHaveBeenLastCalledWith(1, 0, 0.03);
    vi.useRealTimers(); vi.unstubAllGlobals();
  });

  it("gibt die Lautstärke spätestens nach 12 s zurück und bei verworfener Aufnahme sofort", () => {
    const first = setup(true);
    vi.advanceTimersByTime(12_000);
    expect(first.engine.isDucked()).toBe(false);
    vi.useRealTimers(); vi.unstubAllGlobals();
    const second = setup(true);
    second.engine.finish(true);
    expect(second.engine.isDucked()).toBe(false);
    vi.useRealTimers(); vi.unstubAllGlobals();
  });

  it("stoppt ohne aktiven Türsteher wie bisher sofort, auch wenn Jarvis hörbar spricht", () => {
    const { engine, stopControl, start } = setup(true, false);
    expect(start.tentative).toBeUndefined();
    expect(stopControl).toHaveBeenCalledExactlyOnceWith("vad");
    expect(engine.isDucked()).toBe(false);
    vi.useRealTimers(); vi.unstubAllGlobals();
  });

  it("stoppt wie bisher sofort, wenn Jarvis gerade nicht hörbar spricht", () => {
    const { engine, stopControl, start } = setup(false);
    expect(start.tentative).toBeUndefined();
    expect(stopControl).toHaveBeenCalledExactlyOnceWith("vad");
    expect(engine.isDucked()).toBe(false);
    vi.useRealTimers(); vi.unstubAllGlobals();
  });
});
