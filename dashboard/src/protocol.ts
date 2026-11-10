export type AudioHeader = {
  type: "AUDIO";
  stream_id: string;
  sequence: number;
  mime: "audio/mpeg" | "audio/wav";
};

const PCM_RATES = new Set([8000, 16000, 22050, 24000, 44100, 48000]);
/** "audio/pcm;rate=22050" -> 22050 (16-bit Mono, little endian); sonst null. */
export function pcmRate(mime: unknown): number | null {
  if (typeof mime !== "string") return null;
  const match = /^audio\/pcm;rate=(\d{4,5})$/.exec(mime);
  const rate = match ? Number(match[1]) : NaN;
  return PCM_RATES.has(rate) ? rate : null;
}

/** 16-bit-PCM (little endian) -> Float32 im Bereich -1..1. */
export function pcmToFloat(payload: Uint8Array): Float32Array<ArrayBuffer> {
  const samples = Math.floor(payload.byteLength / 2);
  const view = new DataView(payload.buffer, payload.byteOffset, samples * 2);
  const out = new Float32Array(new ArrayBuffer(samples * 4));
  for (let i = 0; i < samples; i++) out[i] = view.getInt16(i * 2, true) / 32768;
  return out;
}

export function parseAudioEnvelope(
  data: ArrayBuffer | Uint8Array,
): { header: AudioHeader; payload: Uint8Array } | null {
  const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
  if (bytes.byteLength < 4) return null;
  const length = new DataView(
    bytes.buffer,
    bytes.byteOffset,
    bytes.byteLength,
  ).getUint32(0, false);
  if (length < 2 || length > bytes.byteLength - 4) return null;
  try {
    const header = JSON.parse(
      new TextDecoder().decode(bytes.subarray(4, 4 + length)),
    ) as Partial<AudioHeader>;
    if (
      header.type !== "AUDIO" ||
      typeof header.stream_id !== "string" ||
      typeof header.sequence !== "number" ||
      !Number.isInteger(header.sequence)
    )
      return null;
    if (header.mime !== "audio/mpeg" && header.mime !== "audio/wav" && pcmRate(header.mime) === null)
      return null;
    return { header: header as AudioHeader, payload: bytes.slice(4 + length) };
  } catch {
    return null;
  }
}

/** A stopped stream is blocked until an explicit server TURN_START opens it again. */
export class PlaybackGate {
  private generation = 0;
  private activeStream: string | null = null;
  private readonly blocked = new Set<string>();
  begin(stream: string): number {
    this.generation++;
    this.activeStream = stream;
    this.blocked.delete(stream);
    return this.generation;
  }
  accept(stream: string): number {
    if (this.blocked.has(stream)) return -1;
    if (this.activeStream !== stream) {
      this.generation++;
      this.activeStream = stream;
    }
    return this.generation;
  }
  invalidate(stream?: string | null): void {
    if (stream === undefined) {
      if (this.activeStream) this.blocked.add(this.activeStream);
      this.activeStream = null;
      this.generation++;
      return;
    }
    if (stream === null) {
      this.invalidate();
      return;
    }
    this.blocked.add(stream);
    if (this.activeStream === stream) {
      this.activeStream = null;
      this.generation++;
    }
  }
  isCurrent(stream: string, generation: number): boolean {
    return (
      generation >= 0 &&
      this.activeStream === stream &&
      this.generation === generation &&
      !this.blocked.has(stream)
    );
  }
  current(): { stream: string | null; generation: number } {
    return { stream: this.activeStream, generation: this.generation };
  }
}

/** Amplitude 0..1 -> dBFS, Untergrenze −60. */
export function dbfs(value: number): number {
  if (!(value > 0)) return -60;
  return Math.max(-60, Math.min(0, 20 * Math.log10(value)));
}
/** VAD im Browser: 20-ms-Frames, Einsatz nach 3 Frames, Ende nach 60 Frames Stille (1,2 s Nachlauf). */
export const VAD_THRESHOLD = 0.022;
export const VAD_ONSET_FRAMES = 3;
export const VAD_HANGOVER_FRAMES = 60;

export class AdaptiveVad {
  private voiced = 0;
  private silence = 0;
  private started = false;
  constructor(
    private readonly threshold = 0.022,
    private readonly onset = 3,
    private readonly hangover = 30,
  ) {}
  push(samples: Float32Array): "start" | "end" | null {
    let energy = 0;
    for (const sample of samples) energy += sample * sample;
    const rms = Math.sqrt(energy / Math.max(1, samples.length));
    if (rms >= this.threshold) {
      this.voiced++;
      this.silence = 0;
    } else {
      this.silence++;
      this.voiced = 0;
    }
    if (!this.started && this.voiced >= this.onset) {
      this.started = true;
      return "start";
    }
    if (this.started && this.silence >= this.hangover) {
      this.started = false;
      return "end";
    }
    return null;
  }
  reset(): void {
    this.voiced = 0;
    this.silence = 0;
    this.started = false;
  }
}

type ResampleCarry = number[] & { phase?: number };
/** Streaming linear resampler. The fractional phase is retained on the carry array. */
export function resampleTo16k(
  input: Float32Array,
  inputRate: number,
  carry: number[] = [],
): { pcm: Int16Array; carry: number[] } {
  if (!Number.isFinite(inputRate) || inputRate <= 0)
    return { pcm: new Int16Array(), carry };
  const ratio = inputRate / 16000;
  const state = carry as ResampleCarry;
  const source = carry.slice();
  source.push(...input);
  let pos = state.phase ?? 0;
  const values: number[] = [];
  while (pos + 1 < source.length) {
    const a = Math.floor(pos),
      f = pos - a;
    values.push(source[a] * (1 - f) + source[a + 1] * f);
    pos += ratio;
  }
  const keepFrom = Math.floor(pos);
  const next = source.slice(keepFrom) as ResampleCarry;
  next.phase = pos - keepFrom;
  const pcm = new Int16Array(values.length);
  for (let i = 0; i < values.length; i++) {
    const v = Math.max(-1, Math.min(1, values[i]));
    pcm[i] = v < 0 ? Math.round(v * 32768) : Math.round(v * 32767);
  }
  return { pcm, carry: next };
}

export function envelope(header: AudioHeader, payload: Uint8Array): Uint8Array {
  const json = new TextEncoder().encode(JSON.stringify(header));
  const out = new Uint8Array(4 + json.length + payload.length);
  new DataView(out.buffer).setUint32(0, json.length, false);
  out.set(json, 4);
  out.set(payload, 4 + json.length);
  return out;
}
