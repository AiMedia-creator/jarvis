import {
  AdaptiveVad,
  dbfs,
  VAD_HANGOVER_FRAMES,
  VAD_ONSET_FRAMES,
  VAD_THRESHOLD,
  PlaybackGate,
  parseAudioEnvelope,
  pcmRate,
  pcmToFloat,
  resampleTo16k,
} from "./protocol";
import type { Status } from "./types";

type StopReason = "vad" | "manual" | "new_turn";
/** Gemessene Browser-Marke der Sprachkette; `t` ist immer performance.now()-Zeit. */
export type AudioMark = {
  name: "vad_start" | "voiced_end" | "aborted" | "audible_start" | "audible_end" | "local_stop" | "audible_stop" | "playback_blocked";
  t: number;
  utterance?: string;
  stream?: string | null;
  cause?: "vad" | "manual" | "limit";
  hangoverMs?: number;
  muted?: boolean;
  /** local_stop: Ende des bereits geplanten Tons (ungesprochener Rest). */
  until?: number;
  /** Wie Kontextzeit auf performance.now() umgerechnet wurde. */
  clock?: "output_timestamp" | "fallback";
};
/** Pegel eines Messfensters in dBFS (null = keine Quelle vorhanden). */
export type Meter = { input: { peak: number; rms: number } | null; output: { peak: number; rms: number } | null; muted: boolean };
type Callbacks = {
  state: (value: Status) => void;
  log: (message: string, level?: "info" | "warn") => void;
  stopControl: (reason: StopReason) => void;
  mark?: (mark: AudioMark) => void;
};

const FRAME_MS = 20;

export class AudioEngine {
  private ctx: AudioContext | null = null;
  private media: MediaStream | null = null;
  private worklet: AudioWorkletNode | null = null;
  private socket: WebSocket | null = null;
  private readonly gate = new PlaybackGate();
  // 20-ms-Frames: Einsatz nach 60 ms; Ende erst nach 1,2 s Pause (Denkpausen in längeren Fragen beenden nichts).
  private hangoverFrames = VAD_HANGOVER_FRAMES;
  private vad = new AdaptiveVad(VAD_THRESHOLD, VAD_ONSET_FRAMES, VAD_HANGOVER_FRAMES);
  /** Reinreden: "frei" = Sprechen stoppt Jarvis sofort; "aus" = während er hörbar spricht, wird nicht aufgenommen. */
  private barge: "frei" | "aus" = "frei";
  /** Sprechmodus: "frei" = Daueraufnahme mit VAD; "ptt" = nur solange gedrückt gehalten wird. */
  private talkMode: "frei" | "ptt" = "frei";
  private carry: number[] = [];
  private preRoll: number[] = [];
  private vadWindow: number[] = [];
  private capturedSamples = 0;
  private lastStream: string | null = null;
  private packetRemainder: number[] = [];
  private pending = new Map<
    number,
    { stream: string; generation: number; payload: Uint8Array; mime: string }
  >();
  /** Gestreamte Sätze kommen als viele kleine PCM-Stücke: Puffer großzügiger, aber weiter begrenzt. */
  private static readonly MAX_PENDING = 96;
  private static readonly MAX_WAITING = 64;
  private turnStartedAt = 0;
  private firstAudioLogged: string | null = null;
  private readonly waiting = new Map<
    string,
    Array<{ sequence: number; payload: Uint8Array; mime: string }>
  >();
  private readonly waitingTimers = new Map<string, number>();
  private readonly startedStreams = new Set<string>();
  private readonly revokedStreams = new Set<string>();
  private decodeChain: Promise<void> = Promise.resolve();
  private gapTimer: ReturnType<typeof setTimeout> | null = null;
  private activeNodes = new Set<AudioBufferSourceNode>();
  private nextSequence: number | null = null;
  private playAt = 0;
  private recording = false;
  private utterance = "";
  private startedAt = 0;
  private closed = false;
  private callbacks: Callbacks;
  /** Pegel nur zur Darstellung (Voice-Core); beeinflusst weder VAD noch Wiedergabe. */
  private inputLevel = 0;
  private analyser: AnalyserNode | null = null;
  private analyserData: Uint8Array<ArrayBuffer> | null = null;
  private analyserFloat: Float32Array<ArrayBuffer> | null = null;
  /** Stumm-Gain hinter dem Analyser: Band misst weiter, was Jarvis sendet. */
  private muteGain: GainNode | null = null;
  private muted = false;
  /** Eingangspegel seit dem letzten meter()-Aufruf, aus den Roh-Samples des Worklets. */
  private inPeak = 0;
  private inSum = 0;
  private inCount = 0;
  private finishCause: "vad" | "manual" | "limit" = "vad";
  private audibleStream: string | null = null;
  private audibleEndCtx = 0;
  private silenceWatch: number | null = null;

  constructor(callbacks: Callbacks) {
    this.callbacks = callbacks;
  }
  attach(socket: WebSocket): void {
    this.socket = socket;
    this.closed = false;
  }

  async unlockPlayback(): Promise<void> {
    if (!this.ctx) this.ctx = new AudioContext();
    if (this.ctx.state === "suspended") await this.ctx.resume();
  }

  private mark(mark: AudioMark): void {
    try {
      this.callbacks.mark?.(mark);
    } catch {
      /* Messung darf die Wiedergabe nie stören */
    }
  }

  /** Ausgabe-Knoten: Quellen → Analyser (Messung) → Stumm-Gain → Lautsprecher. */
  private output(): AudioNode {
    if (!this.analyser && this.ctx) {
      this.analyser = this.ctx.createAnalyser();
      this.analyser.fftSize = 2048; // ≈ 43 ms bei 48 kHz: deckt ein 50-ms-Messfenster fast ganz ab
      this.analyser.smoothingTimeConstant = 0.6;
      this.muteGain = this.ctx.createGain();
      this.muteGain.gain.value = this.muted ? 0 : 1;
      this.analyser.connect(this.muteGain).connect(this.ctx.destination);
      this.analyserData = new Uint8Array(new ArrayBuffer(this.analyser.fftSize));
      this.analyserFloat = new Float32Array(new ArrayBuffer(this.analyser.fftSize * 4));
    }
    return this.analyser ?? this.ctx!.destination;
  }

  /** Ausgabe stumm schalten. Ändert weder Zugzustand noch Serverstrom; das Band misst weiter. */
  /** Sprechpause (Nachlauf), bis eine Aufnahme als beendet gilt: 0,5–2,5 s. */
  setPause(seconds: number): void {
    const frames = Math.round(Math.max(0.5, Math.min(2.5, seconds)) * (1000 / FRAME_MS));
    if (frames === this.hangoverFrames) return;
    this.hangoverFrames = frames;
    this.vad = new AdaptiveVad(VAD_THRESHOLD, VAD_ONSET_FRAMES, frames);
  }
  setBarge(mode: "frei" | "aus"): void {
    this.barge = mode;
  }
  setTalkMode(mode: "frei" | "ptt"): void {
    if (mode === this.talkMode) return;
    this.talkMode = mode;
    if (this.recording) this.finish(true);
    this.vad.reset();
  }
  /** Push-to-talk: Taste/Knopf gedrückt -> Aufnahme beginnt sofort (Mikro muss an sein). */
  pttDown(): boolean {
    if (this.talkMode !== "ptt" || !this.media || this.recording) return false;
    this.beginRecording(performance.now());
    return true;
  }
  pttUp(): void {
    if (this.talkMode !== "ptt" || !this.recording) return;
    this.finishCause = "manual";
    this.finish(performance.now() - this.startedAt < 200);
  }

  setMuted(value: boolean): void {
    this.muted = value;
    if (this.muteGain && this.ctx) this.muteGain.gain.setValueAtTime(value ? 0 : 1, this.ctx.currentTime);
  }
  isMuted(): boolean {
    return this.muted;
  }
  isRecording(): boolean {
    return this.recording;
  }
  micActive(): boolean {
    return this.media !== null;
  }
  /** Wiedergabe gerade hörbar (geplanter Ton hat begonnen und ist nicht zu Ende). */
  isAudible(): boolean {
    if (!this.ctx || this.activeNodes.size === 0) return false;
    return this.ctx.currentTime < this.audibleEndCtx;
  }
  outputLatencyMs(): number | null {
    const latency = (this.ctx as (AudioContext & { outputLatency?: number }) | null)?.outputLatency;
    return typeof latency === "number" && Number.isFinite(latency) ? latency * 1000 : null;
  }

  /** Kontextzeit (s) → performance.now() (ms). Bevorzugt getOutputTimestamp, sonst currentTime + outputLatency. */
  private toPerf(contextTime: number): { t: number; clock: "output_timestamp" | "fallback" } {
    const ctx = this.ctx!;
    const stamp = typeof ctx.getOutputTimestamp === "function" ? ctx.getOutputTimestamp() : null;
    if (stamp && typeof stamp.contextTime === "number" && typeof stamp.performanceTime === "number" && stamp.performanceTime > 0)
      return { t: stamp.performanceTime + (contextTime - stamp.contextTime) * 1000, clock: "output_timestamp" };
    return {
      t: performance.now() + (contextTime - ctx.currentTime) * 1000 + (this.outputLatencyMs() ?? 0),
      clock: "fallback",
    };
  }

  /** Spitzen- und RMS-Pegel (dBFS) seit dem letzten Aufruf; Ausgang aus dem Analyser, unabhängig von aktiven Quellen. */
  meter(): Meter {
    let input: Meter["input"] = null;
    if (this.media) {
      input = this.inCount
        ? { peak: dbfs(this.inPeak), rms: dbfs(Math.sqrt(this.inSum / this.inCount)) }
        : { peak: -60, rms: -60 };
      this.inPeak = 0;
      this.inSum = 0;
      this.inCount = 0;
    }
    let output: Meter["output"] = null;
    if (this.analyser && this.analyserFloat) {
      this.analyser.getFloatTimeDomainData(this.analyserFloat);
      let peak = 0;
      let sum = 0;
      for (const v of this.analyserFloat) {
        const a = Math.abs(v);
        if (a > peak) peak = a;
        sum += v * v;
      }
      output = { peak: dbfs(peak), rms: dbfs(Math.sqrt(sum / this.analyserFloat.length)) };
    }
    return { input, output, muted: this.muted };
  }

  /** Nach einem lokalen Stopp: wann der gemessene Ausgang unter −50 dBFS bleibt (plus Ausgabelatenz). */
  private watchSilence(stream: string | null, started: number): void {
    if (this.silenceWatch !== null) window.clearTimeout(this.silenceWatch);
    const check = () => {
      this.silenceWatch = null;
      if (!this.analyser || !this.analyserFloat) return;
      this.analyser.getFloatTimeDomainData(this.analyserFloat);
      let peak = 0;
      for (const v of this.analyserFloat) peak = Math.max(peak, Math.abs(v));
      const now = performance.now();
      if (dbfs(peak) < -50) {
        this.mark({ name: "audible_stop", t: now + (this.outputLatencyMs() ?? 0), stream, muted: this.muted });
        return;
      }
      if (now - started < 500) this.silenceWatch = window.setTimeout(check, 5);
    };
    this.silenceWatch = window.setTimeout(check, 0);
  }

  /** Aktuelle Pegel 0..1 für die Animation: Mikrofon (nur wenn aktiv) und Sprachausgabe. */
  levels(): { input: number; output: number } {
    let output = 0;
    if (this.analyser && this.analyserData && this.activeNodes.size > 0) {
      this.analyser.getByteTimeDomainData(this.analyserData);
      let sum = 0;
      for (const value of this.analyserData) sum += ((value - 128) / 128) ** 2;
      output = Math.min(1, Math.sqrt(sum / this.analyserData.length) * 4);
    }
    return { input: this.media ? this.inputLevel : 0, output };
  }

  async enableMic(): Promise<void> {
    if (this.media) return;
    this.closed = false;
    await this.unlockPlayback();
    this.media = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });
    await this.ctx!.audioWorklet.addModule("/audio-worklet.js");
    const source = this.ctx!.createMediaStreamSource(this.media);
    this.worklet = new AudioWorkletNode(this.ctx!, "jarvis-mic");
    const mute = this.ctx!.createGain();
    mute.gain.value = 0;
    source.connect(this.worklet).connect(mute).connect(this.ctx!.destination);
    this.worklet.port.onmessage = (
      event: MessageEvent<{ type?: string; samples?: Float32Array }>,
    ) => {
      if (event.data?.type === "samples" && event.data.samples)
        this.accept(event.data.samples);
    };
    this.callbacks.log(
      "Mikrofon explizit aktiviert; clientseitige Energie-VAD aktiv.",
    );
  }

  private accept(samples: Float32Array): void {
    if (!this.ctx || this.closed) return;
    let energy = 0;
    for (const sample of samples) {
      energy += sample * sample;
      const a = Math.abs(sample);
      if (a > this.inPeak) this.inPeak = a;
    }
    this.inSum += energy;
    this.inCount += samples.length;
    const rms = Math.sqrt(energy / Math.max(1, samples.length));
    this.inputLevel = this.inputLevel * 0.7 + Math.min(1, rms * 6) * 0.3;
    const result = resampleTo16k(samples, this.ctx.sampleRate, this.carry);
    this.carry = result.carry;
    this.vadWindow.push(...result.pcm);
    while (this.vadWindow.length >= 320) {
      const frame = this.vadWindow.splice(0, 320);
      this.preRoll.push(...frame);
      if (this.preRoll.length > 3200)
        this.preRoll.splice(0, this.preRoll.length - 3200);
      const normalized = Float32Array.from(frame, (sample) => sample / 32768);
      const ptt = this.talkMode === "ptt";
      const event = ptt ? null : this.vad.push(normalized);
      let onset = false;
      // Reinreden aus: solange Jarvis hörbar spricht, startet keine Aufnahme.
      if (event === "start" && !this.recording && !(this.barge === "aus" && this.isAudible())) {
        // Erster stimmhafter Frame = VAD-Start minus Einsatz-Frames.
        this.beginRecording(performance.now() - VAD_ONSET_FRAMES * FRAME_MS);
        onset = true;
      }
      if (this.recording && !onset) this.sendPcm(frame, false);
      if (event === "end" && this.recording) this.finish(false);
      if (this.recording && this.capturedSamples >= 960000) {
        this.finishCause = "limit";
        this.finish(false); // höchstens 60 s am Stück
      }
    }
    if (
      this.recording &&
      this.socket?.bufferedAmount &&
      this.socket.bufferedAmount > 320000
    ) {
      this.callbacks.log(
        "Audio-Rückstau: Aufnahme sicher abgebrochen.",
        "warn",
      );
      this.finish(true);
    }
  }

  private beginRecording(voicedAt: number): void {
    this.recording = true;
    this.utterance = crypto.randomUUID();
    this.startedAt = performance.now();
    this.capturedSamples = 0;
    this.packetRemainder = [];
    this.finishCause = this.talkMode === "ptt" ? "manual" : "vad";
    this.mark({ name: "vad_start", t: voicedAt, utterance: this.utterance });
    this.stopPlayback();
    this.callbacks.stopControl("vad");
    this.sendJson({ type: "AUDIO_START", utterance_id: this.utterance, sample_rate: 16000 });
    this.sendPcm(this.preRoll, false);
    this.callbacks.state("listening");
    this.callbacks.log(
      this.talkMode === "ptt" ? "Push-to-talk: Aufnahme läuft, solange gehalten wird." : "Sprachbeginn: STOP_STREAM sofort gesendet, 200 ms Vorlauf übertragen.",
    );
  }

  private sendJson(value: object): void {
    if (this.socket?.readyState === WebSocket.OPEN)
      this.socket.send(JSON.stringify(value));
  }
  private sendPcm(samples: number[], flush: boolean): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) return;
    this.packetRemainder.push(...samples);
    while (this.packetRemainder.length >= 320) {
      const frame = new Int16Array(this.packetRemainder.splice(0, 320));
      if (this.socket.bufferedAmount > 320000) {
        this.finish(true);
        return;
      }
      this.socket.send(frame.buffer); // exactly 320 samples / 640 bytes
      this.capturedSamples += 320;
    }
    if (flush && this.packetRemainder.length) {
      const frame = new Int16Array(320);
      frame.set(this.packetRemainder);
      this.packetRemainder = [];
      this.socket.send(frame.buffer);
    }
  }

  finish(abort: boolean): void {
    if (!this.recording) return;
    const id = this.utterance;
    this.recording = false;
    const now = performance.now();
    if (abort) this.mark({ name: "aborted", t: now, utterance: id });
    else {
      const hangoverMs = this.finishCause === "vad" ? this.hangoverFrames * FRAME_MS : 0;
      // t = 0 des Zugs: Ende des letzten stimmhaften Frames (bei VAD-Ende abzüglich Nachlauf).
      this.mark({ name: "voiced_end", t: now - hangoverMs, utterance: id, cause: this.finishCause, hangoverMs });
    }
    this.finishCause = "vad";
    this.sendPcm([], !abort);
    this.sendJson({
      type: abort ? "AUDIO_ABORT" : "AUDIO_END",
      utterance_id: id,
    });
    this.callbacks.state(abort ? "idle" : "transcribing");
    this.callbacks.log(
      abort ? "Aufnahme verworfen." : "Aufnahme beendet; Server-STT folgt.",
    );
  }

  manualStop(): void {
    this.callbacks.stopControl("manual");
    if (this.recording) this.finishCause = "manual";
    if (this.recording) this.finish(performance.now() - this.startedAt < 200);
    else this.stopPlayback();
  }
  turnStarted(stream: string): void {
    this.turnStartedAt = performance.now(); // Messung: Zugbeginn bis erster hörbarer Ton
    this.stopPlayback();
    this.lastStream = stream;
    this.revokedStreams.delete(stream);
    this.startedStreams.add(stream);
    this.gate.begin(stream);
    this.nextSequence = null;
    this.playAt = 0;
    const waiting = this.waiting.get(stream);
    this.waiting.delete(stream);
    const timer = this.waitingTimers.get(stream);
    if (timer) window.clearTimeout(timer);
    this.waitingTimers.delete(stream);
    waiting
      ?.sort((a, b) => a.sequence - b.sequence)
      .forEach((packet) =>
        this.acceptPacket(stream, packet.sequence, packet.payload, packet.mime),
      );
  }

  receive(data: ArrayBuffer | Uint8Array): void {
    const packet = parseAudioEnvelope(data);
    if (!packet) return;
    const stream = packet.header.stream_id;
    // Control and audio websockets can arrive in either order. Never let an audio packet
    // create a new generation: only the authoritative TURN_START may do that.
    if (!this.startedStreams.has(stream)) {
      if (this.revokedStreams.has(stream)) return;
      const list = this.waiting.get(stream) ?? [];
      if (list.length < AudioEngine.MAX_WAITING)
        list.push({
          sequence: packet.header.sequence,
          payload: packet.payload,
          mime: packet.header.mime,
        });
      this.waiting.set(stream, list);
      if (!this.waitingTimers.has(stream))
        this.waitingTimers.set(
          stream,
          window.setTimeout(() => {
            this.waiting.delete(stream);
            this.waitingTimers.delete(stream);
          }, 1200),
        );
      return;
    }
    this.acceptPacket(stream, packet.header.sequence, packet.payload, packet.header.mime);
  }

  private acceptPacket(
    stream: string,
    sequence: number,
    payload: Uint8Array,
    mime = "audio/mpeg",
  ): void {
    const generation = this.gate.accept(stream);
    if (generation < 0) return;
    if (this.ctx === null) {
      this.callbacks.log(
        "Audio empfangen, aber die Wiedergabe ist gesperrt: einmal Mikro oder Senden klicken, damit der Browser Ton erlaubt.",
        "warn",
      );
      this.mark({ name: "playback_blocked", t: performance.now(), stream });
      return;
    }
    if (this.nextSequence === null) this.nextSequence = sequence;
    if (this.pending.size >= AudioEngine.MAX_PENDING) {
      const oldest = Math.min(...this.pending.keys());
      this.pending.delete(oldest);
      this.callbacks.log(
        "Audio-Warteschlange begrenzt; ältesten Satz verworfen.",
        "warn",
      );
    }
    this.pending.set(sequence, { stream, generation, payload, mime });
    this.queueDecode();
  }

  private queueDecode(): void {
    this.decodeChain = this.decodeChain
      .then(async () => {
        while (
          this.nextSequence !== null &&
          this.pending.has(this.nextSequence)
        ) {
          const sequence = this.nextSequence;
          const item = this.pending.get(sequence)!;
          this.pending.delete(sequence);
          this.nextSequence++;
          let buffer: AudioBuffer;
          try {
            const rate = pcmRate(item.mime);
            if (rate !== null) {
              // Gestreamtes Stück: direkt als PCM einplanen (kein Dekodieren, kein Warten auf den ganzen Satz).
              const samples = pcmToFloat(item.payload);
              if (samples.length === 0) continue;
              buffer = this.ctx!.createBuffer(1, samples.length, rate);
              buffer.copyToChannel(samples, 0);
            } else {
              const bytes = new Uint8Array(item.payload);
              const owned = bytes.buffer.slice(0);
              buffer = await this.ctx!.decodeAudioData(owned as ArrayBuffer);
            }
          } catch {
            this.callbacks.log(
              `Audiosatz ${sequence} konnte nicht dekodiert werden.`,
              "warn",
            );
            continue;
          }
          if (!this.gate.isCurrent(item.stream, item.generation)) continue;
          const source = this.ctx!.createBufferSource();
          source.buffer = buffer;
          source.connect(this.output());
          const now = this.ctx!.currentTime;
          this.playAt = Math.max(this.playAt, now + 0.02);
          source.start(this.playAt);
          if (this.firstAudioLogged !== item.stream && this.turnStartedAt > 0) {
            this.firstAudioLogged = item.stream;
            const ms = Math.round(performance.now() - this.turnStartedAt + Math.max(0, this.playAt - now) * 1000);
            this.callbacks.log(`Erster Ton nach ${ms} ms (${pcmRate(item.mime) ? "gestreamt" : "ganzer Satz"}).`);
          }
          if (this.audibleStream !== item.stream) {
            this.audibleStream = item.stream;
            const at = this.toPerf(this.playAt);
            this.mark({ name: "audible_start", t: at.t, stream: item.stream, clock: at.clock, muted: this.muted });
          }
          this.playAt += buffer.duration;
          this.audibleEndCtx = this.playAt;
          const endsAt = this.playAt;
          const stream = item.stream;
          this.activeNodes.add(source);
          source.onended = () => {
            if (!this.activeNodes.delete(source)) return; // gestoppt: zählt als local_stop, nicht als Ende
            if (this.activeNodes.size === 0 && this.ctx) {
              const at = this.toPerf(endsAt);
              this.mark({ name: "audible_end", t: at.t, stream, clock: at.clock });
            }
          };
          this.callbacks.state("speaking");
        }
        if (
          this.nextSequence !== null &&
          this.pending.size > 0 &&
          !this.pending.has(this.nextSequence) &&
          this.gapTimer === null
        ) {
          this.gapTimer = setTimeout(() => {
            this.gapTimer = null;
            if (this.nextSequence === null || this.pending.size === 0) return;
            const available = Math.min(...this.pending.keys());
            if (available > this.nextSequence) {
              this.callbacks.log(
                "Verworfene Audioframes übersprungen; Wiedergabe wird fortgesetzt.",
                "warn",
              );
              this.nextSequence = available;
            }
            this.queueDecode();
          }, 120);
        }
      })
      .catch(() => undefined);
  }

  /** Lokaler Stopp hörbarer Wiedergabe: Marke setzen und das Verstummen am Ausgang messen. */
  private markLocalStop(): void {
    if (this.activeNodes.size === 0) return;
    const now = performance.now();
    const stream = this.audibleStream ?? this.gate.current().stream;
    const until = this.ctx && this.audibleEndCtx > this.ctx.currentTime ? this.toPerf(this.audibleEndCtx).t : undefined;
    this.mark({ name: "local_stop", t: now, stream, muted: this.muted, until });
    this.audibleEndCtx = 0;
    this.watchSilence(stream, now);
  }

  stopPlayback(): void {
    if (this.gapTimer !== null) clearTimeout(this.gapTimer);
    this.gapTimer = null;
    this.markLocalStop();
    this.gate.invalidate();
    this.pending.clear();
    this.nextSequence = null;
    this.playAt = 0;
    for (const source of this.activeNodes) {
      try {
        source.stop();
      } catch {
        /* already ended */
      }
    }
    this.activeNodes.clear();
  }
  currentStreamId(): string | null {
    return this.lastStream;
  }
  onStopped(stream: string | null): void {
    const target = stream ?? this.gate.current().stream;
    if (target) {
      this.revokedStreams.add(target);
      this.startedStreams.delete(target);
      this.waiting.delete(target);
    }
    const current = this.gate.current().stream;
    this.gate.invalidate(stream);
    if (stream === null || stream === current) {
      this.stopNodesOnly();
      this.callbacks.state("idle");
    }
  }
  private stopNodesOnly(): void {
    if (this.gapTimer !== null) clearTimeout(this.gapTimer);
    this.gapTimer = null;
    this.markLocalStop();
    this.pending.clear();
    this.nextSequence = null;
    this.playAt = 0;
    for (const source of this.activeNodes) {
      try {
        source.stop();
      } catch {}
    }
    this.activeNodes.clear();
  }

  /** Stop capture only; text/TTS playback remains available after mic disable. */
  disableMic(): void {
    if (this.recording) this.finish(true);
    this.worklet?.disconnect();
    this.media?.getTracks().forEach((track) => track.stop());
    this.media = null;
    this.worklet = null;
    this.carry = [];
    this.vadWindow = [];
    this.vad.reset();
    this.inputLevel = 0;
    this.inPeak = 0;
    this.inSum = 0;
    this.inCount = 0;
    this.callbacks.log("Mikrofon deaktiviert; Playback bleibt verfügbar.");
  }

  close(): void {
    this.closed = true;
    if (this.recording) this.finish(true);
    this.stopPlayback();
    this.disableMic();
    this.socket = null;
    this.waiting.clear();
    this.waitingTimers.forEach((timer) => window.clearTimeout(timer));
    this.waitingTimers.clear();
    this.startedStreams.clear();
    this.revokedStreams.clear();
    this.lastStream = null;
    const ctx = this.ctx;
    this.ctx = null;
    if (this.silenceWatch !== null) window.clearTimeout(this.silenceWatch);
    this.silenceWatch = null;
    this.analyser = null;
    this.analyserData = null;
    this.analyserFloat = null;
    this.muteGain = null;
    void ctx?.close();
  }
}
