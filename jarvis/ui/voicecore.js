/* Jarvis Voice-Core: Zustandsmaschine, austauschbare Audioquellen und Canvas-Renderer.
 *
 *  VoiceState           – die erlaubten Zustände
 *  VoiceCoreController  – hält den Zustand, berechnet Pegel, steuert den Renderer
 *  AudioReactiveSource  – Schnittstelle { name, level(): 0..1, start(), stop() }
 *     MicSource         – Mikrofon über Web Audio (Browser-Modus)
 *     MediaOutputSource – Lautstärke der eigenen Sprachausgabe (<audio>)
 *     FunctionSource    – beliebige Funktion, z. B. ElevenLabs getInputVolume()/getOutputVolume()
 *     MockSource        – künstlicher Sprachpegel zum Vorführen ohne Mikrofon
 *  VoiceCoreRenderer    – zeichnet Kern, Ringe, Glow, Partikel und Wellenform (requestAnimationFrame)
 *
 * Die Oberfläche kennt keinen konkreten Sprach-Anbieter: sie setzt nur Zustände und Audioquellen.
 */
(function (global) {
  "use strict";

  const VoiceState = Object.freeze({
    IDLE: "IDLE", LISTENING: "LISTENING", TRANSCRIBING: "TRANSCRIBING", THINKING: "THINKING", SPEAKING: "SPEAKING",
    INTERRUPTED: "INTERRUPTED", WAITING_FOR_APPROVAL: "WAITING_FOR_APPROVAL", ERROR: "ERROR",
  });

  // Zentrale Animationseinstellungen (Farben als RGB, Tempo in Zyklen pro Sekunde).
  const CONFIG = {
    intensity: 1.0,
    states: {
      IDLE:                 { label: "Bereit",             color: [255, 149, 0],   core: [255, 226, 180], scale: 1.00, breathe: 0.02, breatheHz: 0.18, ringSpeed: 0.04, react: 0.0, particlesIn: 0 },
      LISTENING:            { label: "Ich höre zu",        color: [6, 182, 212],   core: [220, 250, 255], scale: 1.12, breathe: 0.015, breatheHz: 0.5, ringSpeed: 0.10, react: 0.55, particlesIn: 0 },
      TRANSCRIBING:         { label: "Du sprichst",        color: [34, 211, 238],  core: [240, 254, 255], scale: 1.18, breathe: 0.0, breatheHz: 0.0, ringSpeed: 0.16, react: 0.8, particlesIn: 0 },
      INTERRUPTED:          { label: "Unterbrochen",       color: [235, 238, 240], core: [255, 255, 255], scale: 0.9, breathe: 0.0, breatheHz: 0.0, ringSpeed: 0.0, react: 0.0, particlesIn: 0 },
      THINKING:             { label: "Verarbeite Auftrag", color: [255, 122, 0],   core: [255, 220, 170], scale: 1.02, breathe: 0.035, breatheHz: 0.9, ringSpeed: 0.45, react: 0.0, particlesIn: 1 },
      SPEAKING:             { label: "Spreche",            color: [255, 170, 40],  core: [255, 244, 225], scale: 1.08, breathe: 0.01, breatheHz: 0.3, ringSpeed: 0.12, react: 0.7, particlesIn: 0 },
      WAITING_FOR_APPROVAL: { label: "Wartet auf Freigabe", color: [239, 68, 68], core: [255, 205, 200], scale: 0.98, breathe: 0.03, breatheHz: 0.12, ringSpeed: 0.02, react: 0.0, particlesIn: 0 },
      ERROR:                { label: "Fehler",             color: [239, 68, 68],   core: [255, 160, 160], scale: 0.94, breathe: 0.0, breatheHz: 0.0, ringSpeed: 0.0, react: 0.0, particlesIn: 0, dashed: true },
    },
  };

  // ---------- Audioquellen ----------
  class AudioReactiveSource { constructor(name) { this.name = name; } start() {} stop() {} level() { return 0; } }

  let sharedCtx = null;
  function audioCtx() {
    const AC = global.AudioContext || global.webkitAudioContext;
    if (!AC) return null;
    if (!sharedCtx) sharedCtx = new AC();
    if (sharedCtx.state === "suspended") sharedCtx.resume().catch(() => {});
    return sharedCtx;
  }
  function rms(analyser, buf) {
    analyser.getFloatTimeDomainData(buf);
    let s = 0; for (let i = 0; i < buf.length; i++) s += buf[i] * buf[i];
    return Math.sqrt(s / buf.length);
  }

  class MicSource extends AudioReactiveSource {
    constructor() { super("Mikrofon"); this.stream = null; this.analyser = null; this.buf = null; }
    async start() {
      if (this.stream) return;
      const ctx = audioCtx(); if (!ctx || !navigator.mediaDevices) return;
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const src = ctx.createMediaStreamSource(this.stream);
      this.analyser = ctx.createAnalyser(); this.analyser.fftSize = 1024;
      this.buf = new Float32Array(this.analyser.fftSize); src.connect(this.analyser);
    }
    stop() { if (this.stream) this.stream.getTracks().forEach(t => t.stop()); this.stream = null; this.analyser = null; }
    level() { return this.analyser ? Math.min(1, rms(this.analyser, this.buf) * 6) : 0; }
  }

  class MediaOutputSource extends AudioReactiveSource {
    constructor() { super("Sprachausgabe"); this.analyser = null; this.buf = null; }
    attach(audioEl) {
      const ctx = audioCtx(); if (!ctx || ctx.state !== "running") return;  // sonst bliebe der Ton stumm
      try {
        const src = ctx.createMediaElementSource(audioEl);
        if (!this.analyser) { this.analyser = ctx.createAnalyser(); this.analyser.fftSize = 1024; this.buf = new Float32Array(1024); this.analyser.connect(ctx.destination); }
        src.connect(this.analyser);
      } catch (e) { /* Element schon verbunden oder nicht erlaubt */ }
    }
    level() { return this.analyser ? Math.min(1, rms(this.analyser, this.buf) * 4) : 0; }
  }

  class FunctionSource extends AudioReactiveSource {
    constructor(name, fn) { super(name); this.fn = fn; }
    level() { try { return Math.max(0, Math.min(1, Number(this.fn()) || 0)); } catch (e) { return 0; } }
  }

  class MockSource extends AudioReactiveSource {
    constructor() { super("Mock"); this.t0 = performance.now(); }
    level() {
      const t = (performance.now() - this.t0) / 1000;
      const syll = Math.max(0, Math.sin(t * 9.0) * Math.sin(t * 2.3 + 1)) ;        // Silben
      const phrase = 0.5 + 0.5 * Math.sin(t * 0.7);                               // Satzmelodie
      return Math.min(1, syll * phrase * 1.5 + Math.random() * 0.06);
    }
  }

  // ---------- Controller ----------
  class VoiceCoreController {
    constructor(opts) {
      this.state = VoiceState.IDLE;
      this.listeners = [];
      this.input = null;    // Quelle für LISTENING
      this.output = null;   // Quelle für SPEAKING
      this.smoothed = 0;
      this.reducedMotion = !!(opts && opts.reducedMotion);
      this.renderer = opts && opts.canvas ? new VoiceCoreRenderer(opts.canvas, this) : null;
    }
    setState(next) {
      if (!VoiceState[next]) throw new Error("Unbekannter Zustand: " + next);
      if (next === this.state) return;
      const prev = this.state; this.state = next;
      this.listeners.forEach(fn => fn(next, prev));
    }
    onChange(fn) { this.listeners.push(fn); }
    setInput(src) { this.input = src; }
    setOutput(src) { this.output = src; }
    rawLevel() {
      const s = this.state;
      if ((s === VoiceState.LISTENING || s === VoiceState.TRANSCRIBING) && this.input) return this.input.level();
      if (s === VoiceState.SPEAKING && this.output) return this.output.level();
      return 0;
    }
    level() {  // geglätteter Pegel 0..1
      const raw = this.rawLevel();
      this.smoothed += (raw - this.smoothed) * (raw > this.smoothed ? 0.45 : 0.12);
      return this.smoothed;
    }
    get config() { return CONFIG.states[this.state]; }
    label() { return CONFIG.states[this.state].label; }
    setReducedMotion(v) { this.reducedMotion = !!v; if (this.renderer) this.renderer.kick(); }
  }

  // ---------- Renderer ----------
  class VoiceCoreRenderer {
    constructor(canvas, ctrl) {
      this.c = canvas; this.g = canvas.getContext("2d"); this.ctrl = ctrl;
      this.t = 0; this.last = performance.now(); this.rot = [0, 0, 0];
      this.col = CONFIG.states.IDLE.color.slice(); this.coreCol = CONFIG.states.IDLE.core.slice(); this.scale = 1;
      this.particles = Array.from({ length: 70 }, () => this.newParticle(true));
      this.wave = new Array(64).fill(0);
      this.running = true;
      const ro = global.ResizeObserver ? new ResizeObserver(() => this.resize()) : null;
      if (ro) ro.observe(canvas); this.resize();
      requestAnimationFrame(this.frame.bind(this));
    }
    newParticle(spread) {
      const a = Math.random() * Math.PI * 2;
      return { a, r: spread ? 0.55 + Math.random() * 0.45 : 0.95 + Math.random() * 0.08, s: (Math.random() - 0.5) * 0.12, z: Math.random() };
    }
    resize() {
      const dpr = Math.min(global.devicePixelRatio || 1, 2);
      const w = this.c.clientWidth || 300, h = this.c.clientHeight || 300;
      this.c.width = Math.round(w * dpr); this.c.height = Math.round(h * dpr); this.dpr = dpr;
    }
    kick() { this.last = performance.now(); }
    frame(now) {
      if (!this.running) return;
      const dt = Math.min(0.05, (now - this.last) / 1000); this.last = now;
      const reduced = this.ctrl.reducedMotion;
      this.t += reduced ? 0 : dt;
      const cfg = this.ctrl.config; const lvl = this.ctrl.level();
      const k = 1 - Math.pow(0.002, dt);        // weicher Farb-/Größenübergang
      for (let i = 0; i < 3; i++) { this.col[i] += (cfg.color[i] - this.col[i]) * k; this.coreCol[i] += (cfg.core[i] - this.coreCol[i]) * k; }
      const breathe = reduced ? 0 : Math.sin(this.t * Math.PI * 2 * cfg.breatheHz) * cfg.breathe;
      const target = cfg.scale + breathe + (reduced ? 0 : lvl * cfg.react * 0.5 * CONFIG.intensity);
      this.scale += (target - this.scale) * k;
      if (!reduced) { this.rot[0] += dt * cfg.ringSpeed * Math.PI * 2; this.rot[1] -= dt * cfg.ringSpeed * 1.4 * Math.PI * 2; this.rot[2] += dt * cfg.ringSpeed * 0.6 * Math.PI * 2; }
      this.wave.push(lvl); this.wave.shift();
      this.draw(cfg, lvl, reduced);
      requestAnimationFrame(this.frame.bind(this));
    }
    draw(cfg, lvl, reduced) {
      const g = this.g, W = this.c.width, H = this.c.height, cx = W / 2, cy = H / 2;
      const R = Math.min(W, H) * 0.36 * this.scale;
      const [r, gg, b] = this.col.map(Math.round), rgb = (a) => `rgba(${r},${gg},${b},${a})`;
      const [cr, cg, cb] = this.coreCol.map(Math.round);
      g.clearRect(0, 0, W, H);

      // Außen-Glow
      const glow = g.createRadialGradient(cx, cy, R * 0.2, cx, cy, R * 1.55);
      glow.addColorStop(0, rgb(0.28 + lvl * 0.25)); glow.addColorStop(0.45, rgb(0.08 + lvl * 0.08)); glow.addColorStop(1, rgb(0));
      g.fillStyle = glow; g.beginPath(); g.arc(cx, cy, R * 1.55, 0, Math.PI * 2); g.fill();

      // Partikel + Neuralnetz
      if (!reduced) {
        const pts = [];
        for (const p of this.particles) {
          p.a += p.s * 0.016 * (1 + cfg.ringSpeed * 3);
          if (cfg.particlesIn) { p.r -= 0.004; if (p.r < 0.32) Object.assign(p, this.newParticle(false)); }
          else if (p.r < 0.55) p.r += 0.002;
          const rr = R * (1.05 + p.r * 0.45) * (1 + lvl * 0.15 * p.z);
          pts.push([cx + Math.cos(p.a) * rr, cy + Math.sin(p.a) * rr, p.z]);
        }
        g.lineWidth = this.dpr * 0.6;
        for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) {
          const dx = pts[i][0] - pts[j][0], dy = pts[i][1] - pts[j][1], d = dx * dx + dy * dy, lim = (R * 0.28) ** 2;
          if (d < lim) { g.strokeStyle = rgb(0.10 * (1 - d / lim)); g.beginPath(); g.moveTo(pts[i][0], pts[i][1]); g.lineTo(pts[j][0], pts[j][1]); g.stroke(); }
        }
        for (const [x, y, z] of pts) { g.fillStyle = rgb(0.35 + z * 0.5); g.beginPath(); g.arc(x, y, this.dpr * (1 + z * 1.6), 0, Math.PI * 2); g.fill(); }
      }

      // Ringe
      const rings = [[1.0, 0.55, 1.4, 0], [1.16, 0.30, 1.0, 1], [1.32, 0.16, 0.8, 2]];
      for (const [f, a, w, idx] of rings) {
        const rr = R * f * (1 + lvl * (0.06 + idx * 0.05));
        g.save(); g.translate(cx, cy); g.rotate(this.rot[idx]);
        g.strokeStyle = rgb(a); g.lineWidth = this.dpr * w;
        if (cfg.dashed || idx === 1) g.setLineDash([this.dpr * (cfg.dashed ? 3 : 10), this.dpr * (cfg.dashed ? 6 : 14)]);
        g.beginPath(); g.arc(0, 0, rr, idx === 2 ? 0.2 : 0, idx === 2 ? Math.PI * 1.75 : Math.PI * 2); g.stroke();
        g.restore();
      }

      // Kern
      const core = g.createRadialGradient(cx - R * 0.12, cy - R * 0.14, R * 0.05, cx, cy, R * 0.62);
      core.addColorStop(0, `rgba(${cr},${cg},${cb},1)`); core.addColorStop(0.35, rgb(0.95)); core.addColorStop(0.8, rgb(0.35)); core.addColorStop(1, rgb(0));
      g.fillStyle = core; g.beginPath(); g.arc(cx, cy, R * 0.62, 0, Math.PI * 2); g.fill();

      // Wellenform (unten, schmal)
      const ww = R * 1.6, wy = cy + R * 1.42, n = this.wave.length;
      g.strokeStyle = rgb(0.55); g.lineWidth = this.dpr * 1.2; g.beginPath();
      for (let i = 0; i < n; i++) {
        const x = cx - ww / 2 + (ww * i) / (n - 1);
        const amp = this.wave[i] * R * 0.18 * Math.sin(i * 0.9 + this.t * 8);
        i ? g.lineTo(x, wy + amp) : g.moveTo(x, wy + amp);
      }
      g.stroke();
    }
  }

  // Muss nach einer Nutzeraktion (Klick/Taste) aufgerufen werden, damit der Browser Audio-Analyse erlaubt.
  function unlockAudio() { audioCtx(); }

  global.JarvisVoice = { unlockAudio, VoiceState, CONFIG, VoiceCoreController, AudioReactiveSource, MicSource, MediaOutputSource, FunctionSource, MockSource };
})(window);
