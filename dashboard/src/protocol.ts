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
/**
 * VAD im Browser: 20-ms-Frames. Einsatz nach 80 ms durchgehender Stimme (kurze Knackser lösen nichts aus, „Stopp“
 * und „Ja“ schon), Ende nach 60 Frames Stille (1,2 s Nachlauf). Während Jarvis hörbar spricht, zählt Reinreden erst
 * bei 140 ms Stimme innerhalb von 200 ms über dem gemessenen Lautsprecher-Echo – sonst unterbricht er sich über das
 * Mikrofon selbst.
 */
export const VAD_THRESHOLD = 0.022;
export const VAD_ONSET_FRAMES = 4;
export const VAD_ONSET_FRAMES_PLAYBACK = 7;
/** Fenster, in dem die Reinreden-Frames liegen müssen (Sprache hat kurze Lücken zwischen Silben). */
export const VAD_PLAYBACK_WINDOW = 10;
export const VAD_HANGOVER_FRAMES = 60;
/** Obergrenze der Schwelle aus dem Grundrauschen: lauter Raum darf normale Sprache nicht aussperren. */
const VAD_MAX_NOISE_THRESHOLD = 0.1;
/** Schwelle = Vielfaches des Grundrauschens bzw. des Echo-Pegels. */
const NOISE_MARGIN = 3;
const ECHO_MARGIN = 1.3;
/** Echo-Hüllkurve fällt langsam (Halbwert ≈ 2,8 s): Satzpausen der Ausgabe senken die Schwelle kaum. */
const ECHO_DECAY = 0.995;
/** Nach Beginn der Wiedergabe lernt die Hüllkurve erst den Echo-Pegel, bevor Reinreden zählt. */
const ECHO_GRACE_FRAMES = 15;

export class AdaptiveVad {
  private voiced = 0;
  private silence = 0;
  private started = false;
  /** Grundrauschen des Raums (RMS), folgt leisen Frames schnell nach unten und steigt nur langsam. */
  private floor: number;
  /** Pegel, mit dem Jarvis' eigene Stimme am Mikrofon ankommt (Spitzen-Hüllkurve während der Wiedergabe). */
  private echo = 0;
  private playbackFrames = 0;
  private idleFrames = 0;
  /** Stimmhaft/nicht stimmhaft der letzten Frames während der Wiedergabe (Fenster für Reinreden). */
  private recent: boolean[] = [];
  private usedOnset: number;
  private hangover: number;
  constructor(
    private readonly threshold = VAD_THRESHOLD,
    private readonly onset = VAD_ONSET_FRAMES,
    hangover = 30,
    private readonly playbackOnset = Math.max(onset, VAD_ONSET_FRAMES_PLAYBACK),
  ) {
    this.hangover = hangover;
    this.floor = threshold / NOISE_MARGIN;
    this.usedOnset = onset;
  }
  /** Aktuelle Einsatz-Schwelle (RMS); `playback` = Jarvis ist gerade hörbar. */
  currentThreshold(playback = false): number {
    const noise = Math.min(VAD_MAX_NOISE_THRESHOLD, this.floor * NOISE_MARGIN);
    const quiet = Math.max(this.threshold, noise);
    // Eine laufende Aufnahme behält die normale Schwelle: der Nutzer spricht bereits.
    if (!playback || this.started) return quiet;
    return Math.max(quiet, this.threshold * 2, this.echo * ECHO_MARGIN);
  }
  /** Frames, nach denen der letzte „start“ ausgelöst hat (für die Rückrechnung auf den Sprachbeginn). */
  onsetFrames(): number {
    return this.usedOnset;
  }
  setHangover(frames: number): void {
    this.hangover = frames;
  }
  push(samples: Float32Array, playback = false): "start" | "end" | null {
    let energy = 0;
    for (const sample of samples) energy += sample * sample;
    const rms = Math.sqrt(energy / Math.max(1, samples.length));
    const isVoiced = rms >= this.currentThreshold(playback);
    this.echo *= ECHO_DECAY;
    if (playback) {
      // Nach einer längeren Pause der Ausgabe neu einmessen (Lautstärke kann sich geändert haben).
      if (this.idleFrames > 50) this.playbackFrames = 0;
      this.idleFrames = 0;
      this.playbackFrames++;
      // Mögliche Sprache des Nutzers hebt die Echo-Schätzung nicht an – sonst käme Reinreden nie durch.
      if (!this.started && (!isVoiced || this.playbackFrames <= ECHO_GRACE_FRAMES)) this.echo = Math.max(this.echo, rms);
    } else {
      this.idleFrames++;
      // Grundrauschen nur ohne Wiedergabe messen; Dauerrauschen (Lüfter) hebt die Schwelle, statt endlos aufzunehmen.
      this.floor = rms < this.floor ? this.floor * 0.9 + rms * 0.1 : Math.min(1, this.floor * 1.003);
    }
    if (isVoiced) {
      this.voiced++;
      this.silence = 0;
    } else {
      this.silence++;
      this.voiced = 0;
    }
    if (!this.started) {
      if (!playback) {
        this.recent.length = 0;
        if (this.voiced >= this.onset) return this.begin(this.onset);
        return null;
      }
      this.recent.push(isVoiced);
      if (this.recent.length > VAD_PLAYBACK_WINDOW) this.recent.shift();
      const learning = this.playbackFrames <= ECHO_GRACE_FRAMES;
      if (!learning && isVoiced && this.recent.filter(Boolean).length >= this.playbackOnset) return this.begin(this.recent.length);
      return null;
    }
    if (this.silence >= this.hangover) {
      this.started = false;
      return "end";
    }
    return null;
  }
  private begin(frames: number): "start" {
    this.started = true;
    this.usedOnset = frames;
    this.recent.length = 0;
    return "start";
  }
  /** Setzt die Erkennung zurück; das gelernte Grundrauschen bleibt (derselbe Raum, dasselbe Mikrofon). */
  reset(): void {
    this.voiced = 0;
    this.silence = 0;
    this.started = false;
    this.recent.length = 0;
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
