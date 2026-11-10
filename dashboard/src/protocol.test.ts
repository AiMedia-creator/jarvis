import { describe, it, expect } from "vitest";
import {
  parseAudioEnvelope,
  envelope,
  PlaybackGate,
  AdaptiveVad,
  resampleTo16k,
} from "./protocol";
describe("Audio envelope", () => {
  it("liest 4B-Big-Endian Header und Payload", () => {
    const p = new Uint8Array([1, 2, 3]);
    const x = envelope(
      { type: "AUDIO", stream_id: "s", sequence: 1, mime: "audio/wav" },
      p,
    );
    const r = parseAudioEnvelope(x);
    expect(r?.header.stream_id).toBe("s");
    expect([...r!.payload]).toEqual([1, 2, 3]);
  });
  it("verwirft defekte Längen", () => {
    expect(parseAudioEnvelope(new Uint8Array([0, 0, 0, 99]))).toBeNull();
  });
});
describe("Playback generations", () => {
  it("verwirft stale stream after invalidation", () => {
    const g = new PlaybackGate();
    const n = g.accept("a");
    g.invalidate("a");
    expect(g.isCurrent("a", n)).toBe(false);
  });
  it("allows ordered same stream", () => {
    const g = new PlaybackGate();
    const n = g.accept("a");
    expect(g.isCurrent("a", n)).toBe(true);
  });
});
describe("VAD / resampling", () => {
  it("detects onset and hangover", () => {
    const v = new AdaptiveVad(0.01, 2, 2);
    expect(v.push(new Float32Array([0.1, 0.1]))).toBeNull();
    expect(v.push(new Float32Array([0.1, 0.1]))).toBe("start");
    expect(v.push(new Float32Array(2))).toBeNull();
    expect(v.push(new Float32Array(2))).toBe("end");
  });
  it("resamples 48k to 16k", () => {
    const r = resampleTo16k(new Float32Array(480), 48000);
    expect(r.pcm.length).toBe(160);
  });
});

describe("Regression guards", () => {
  it("does not resurrect a stopped stream without TURN_START", () => {
    const g = new PlaybackGate();
    g.accept("old");
    g.invalidate("old");
    expect(g.accept("old")).toBe(-1);
    const n = g.begin("new");
    expect(g.isCurrent("new", n)).toBe(true);
  });
  it("keeps cumulative boundaries over fractional 44.1k chunks", () => {
    let carry: number[] = [];
    let total = 0;
    for (let i = 0; i < 10; i++) {
      const r = resampleTo16k(new Float32Array(441), 44100, carry);
      carry = r.carry;
      total += r.pcm.length;
    }
    expect(total).toBe(1600);
  });
});

import { pcmRate, pcmToFloat } from "./protocol";
describe("PCM-Streaming", () => {
  it("akzeptiert nur bekannte PCM-Raten und wandelt 16-bit little endian korrekt", () => {
    expect(pcmRate("audio/pcm;rate=22050")).toBe(22050);
    expect(pcmRate("audio/pcm;rate=12345")).toBeNull();
    expect(pcmRate("audio/mpeg")).toBeNull();
    const bytes = new Uint8Array([0x00, 0x80, 0xff, 0x7f, 0x00, 0x00, 0x01]); // -32768, 32767, 0, Rest-Byte
    const out = pcmToFloat(bytes);
    expect(out.length).toBe(3);
    expect(out[0]).toBe(-1);
    expect(out[1]).toBeCloseTo(32767 / 32768);
    expect(out[2]).toBe(0);
  });
  it("Hülle mit PCM-Format wird angenommen, fremdes Format nicht", () => {
    const make = (mime: string) => {
      const header = new TextEncoder().encode(JSON.stringify({ type: "AUDIO", stream_id: "s1", sequence: 0, mime }));
      const out = new Uint8Array(4 + header.length + 2);
      new DataView(out.buffer).setUint32(0, header.length, false);
      out.set(header, 4);
      return out;
    };
    expect(parseAudioEnvelope(make("audio/pcm;rate=22050"))?.header.mime).toBe("audio/pcm;rate=22050");
    expect(parseAudioEnvelope(make("audio/pcm;rate=1"))).toBeNull();
  });
});
