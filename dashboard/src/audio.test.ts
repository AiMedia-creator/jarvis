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
