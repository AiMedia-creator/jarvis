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
  const frame = (level: number) => new Float32Array(320).fill(level);
  const feed = (v: AdaptiveVad, level: number, frames: number, playback = false) => {
    const events: string[] = [];
    for (let i = 0; i < frames; i++) { const e = v.push(frame(level), playback); if (e) events.push(e); }
    return events;
  };
  it("ignoriert Klicks und Tastenanschläge (kürzer als der Einsatz)", () => {
    const v = new AdaptiveVad();
    expect(feed(v, 0.001, 50)).toEqual([]);
    expect(feed(v, 0.4, 3)).toEqual([]); // 60 ms Knall
    expect(feed(v, 0.001, 20)).toEqual([]);
    expect(feed(v, 0.2, 3)).toEqual([]);
    expect(feed(v, 0.2, 1)).toEqual(["start"]); // 80 ms Stimme: auch ein kurzes „Stopp“ reicht
    expect(v.onsetFrames()).toBe(4);
  });
  it("gewöhnt sich an Dauerrauschen statt endlos aufzunehmen, Sprache darüber startet weiter", () => {
    const v = new AdaptiveVad(0.022, 6, 60);
    const noise = feed(v, 0.03, 1500); // 30 s Lüfter knapp über der festen Schwelle
    expect(noise.filter((e) => e === "start").length).toBeLessThanOrEqual(1);
    expect(noise.at(-1) ?? "end").toBe("end");
    expect(feed(v, 0.03, 500)).toEqual([]);
    expect(feed(v, 0.25, 6)).toEqual(["start"]);
  });
  it("bleibt in stiller Umgebung bei der Grundschwelle", () => {
    const v = new AdaptiveVad();
    feed(v, 0.0005, 500);
    expect(v.currentThreshold()).toBeCloseTo(0.022, 5);
  });
  it("unterbricht Jarvis nicht durch sein eigenes Echo, wohl aber durch den Nutzer", () => {
    const v = new AdaptiveVad();
    feed(v, 0.001, 100);
    // Jarvis spricht: am Mikrofon kommen Silben mit 0,06–0,09 an (weit über der festen Schwelle 0,022).
    const events: string[] = [];
    for (let i = 0; i < 400; i++) {
      const level = i % 14 < 10 ? 0.06 + 0.03 * ((i * 7) % 10) / 10 : 0.004;
      const e = v.push(frame(level), true);
      if (e) events.push(e);
    }
    expect(events).toEqual([]);
    // Der Nutzer redet lauter rein, mit Silbenlücken: 140 ms Stimme innerhalb von 200 ms zählen als Sprachbeginn.
    const reinreden = [0.2, 0.22, 0.18, 0.01, 0.2, 0.21, 0.19, 0.2];
    const got = reinreden.map((level) => v.push(frame(level), true));
    expect(got.slice(0, 7).every((e) => e === null)).toBe(true);
    expect(got[7]).toBe("start");
  });
  it("lernt den Echo-Pegel in den ersten 300 ms der Wiedergabe, ohne auszulösen", () => {
    const v = new AdaptiveVad();
    feed(v, 0.001, 100);
    expect(feed(v, 0.12, 200, true)).toEqual([]); // lautes Echo ab dem ersten Ton
  });
  it("übernimmt eine geänderte Sprechpause, ohne das Gelernte zu verlieren", () => {
    const v = new AdaptiveVad(0.022, 6, 60);
    feed(v, 0.2, 6);
    v.setHangover(25);
    expect(feed(v, 0.001, 24)).toEqual([]);
    expect(feed(v, 0.001, 1)).toEqual(["end"]);
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
