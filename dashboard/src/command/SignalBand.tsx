import { useEffect, useRef, useState } from "react";
import type { AudioEngine, Meter } from "../audio";
import { dbfs, VAD_THRESHOLD } from "../protocol";

const MIC = "#3F78B5";
const VOICE = "#1E7F72";
const LINE = "#DFE5EB";
const MUTED = "#637083";
const COL = 3; // 2 px Säule + 1 px Abstand, eine Säule je 50 ms
const HEIGHT = 70;
const VAD_DB = dbfs(VAD_THRESHOLD); // ≈ −33 dBFS auf der RMS-Skala

type Column = { i: number | null; o: number | null };
const fmtDb = (v: number | null | undefined) => (v == null ? "–" : v <= -60 ? "−60" : `−${Math.abs(Math.round(v))}`.replace("−0", "0"));

/** Gespiegeltes Signalband: MIC nach oben, JARVIS nach unten; Spitzenwerte je 50 ms, −60…0 dBFS. */
export type BandMeter = Meter & { relative?: { input: number; output: number } };

export function SignalBand({ source, connected, disconnectedAt, reduced, redrawKey }: {
  /** Messquelle: AudioEngine (dBFS) oder ElevenLabs-Client (relative Pegel 0–100 %); null = kein Audiokanal. */
  source: (() => BandMeter | null) | null; connected: boolean; disconnectedAt: number | null; reduced: boolean; redrawKey: number;
}) {
  const sourceRef = useRef(source);
  sourceRef.current = source;
  const hasSource = source !== null;
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const cols = useRef<Column[]>([]);
  const latest = useRef<BandMeter | null>(null);
  const [numbers, setNumbers] = useState<BandMeter | null>(null);
  const [width, setWidth] = useState(600);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(120, Math.floor(entry.contentRect.width))));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const draw = () => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx) return;
    const dpr = window.devicePixelRatio || 1;
    if (canvas.width !== width * dpr) {
      canvas.width = width * dpr;
      canvas.height = HEIGHT * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, HEIGHT);
    const mid = HEIGHT / 2;
    const span = mid - 3;
    const h = (db: number) => Math.max(0, ((db + 60) / 60) * span);
    // Grundlinie (Stille)
    ctx.fillStyle = LINE;
    ctx.fillRect(0, mid, width, 1);
    // VAD-Schwelle (RMS-Skala), gestrichelt
    ctx.strokeStyle = MUTED;
    ctx.setLineDash([3, 3]);
    ctx.lineWidth = 1;
    ctx.beginPath();
    const y = Math.round(mid - h(VAD_DB)) + 0.5;
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
    ctx.setLineDash([]);
    // Säulen: rechter Rand = jetzt (reduzierte Bewegung: festes Fenster der letzten 10 s)
    const visible = reduced ? cols.current.slice(-200) : cols.current.slice(-Math.floor(width / COL));
    const step = reduced ? width / 200 : COL;
    const start = width - visible.length * step;
    visible.forEach((c, index) => {
      const x = start + index * step;
      const w = Math.max(1, step - 1);
      if (c.i != null && c.i > -60) {
        ctx.fillStyle = MIC;
        const v = h(c.i);
        ctx.fillRect(x, mid - v, w, v);
      }
      if (c.o != null && c.o > -60) {
        ctx.fillStyle = VOICE;
        const v = h(c.o);
        ctx.fillRect(x, mid + 1, w, v);
      }
    });
  };
  const drawRef = useRef(draw);
  drawRef.current = draw;

  useEffect(() => {
    let tick = 0;
    const timer = window.setInterval(() => {
      const m = sourceRef.current ? sourceRef.current() : null;
      latest.current = m;
      cols.current.push({ i: m?.input?.peak ?? null, o: m?.output?.peak ?? null });
      if (cols.current.length > 1200) cols.current.splice(0, cols.current.length - 1200);
      if (!reduced) drawRef.current();
      tick++;
      // Zahlen: 4 Hz, bei reduzierter Bewegung 2 Hz
      if (tick % (reduced ? 10 : 5) === 0) setNumbers(m);
    }, 50);
    return () => window.clearInterval(timer);
  }, [hasSource, reduced]);

  // Reduzierte Bewegung: neu zeichnen nur bei Änderungen der Messdaten (z. B. Zugende) und Größenwechsel.
  useEffect(() => { drawRef.current(); }, [redrawKey, width, reduced]);

  const since = disconnectedAt != null ? new Date(Date.now() - (performance.now() - disconnectedAt)).toLocaleTimeString("de-DE") : null;
  const micOff = !numbers?.input;
  return (
    <section className="cc-band" aria-label="Signalband: Pegel von Mikrofon und Jarvis-Stimme">
      <div className="cc-band-axis" aria-hidden="true"><span>MIC</span><span>JARVIS</span></div>
      <div className="cc-band-plot" ref={wrapRef}>
        <canvas ref={canvasRef} style={{ width: "100%", height: HEIGHT }} aria-hidden="true" />
        <span className="cc-band-vad" style={{ top: Math.round(HEIGHT / 2 - ((VAD_DB + 60) / 60) * (HEIGHT / 2 - 3)) - 15 }}>
          VAD-Schwelle (RMS) {fmtDb(VAD_DB)} dBFS
        </span>
        {!connected && <span className="cc-band-empty">kein Signal{since ? ` seit ${since}` : ""}</span>}
        {connected && !hasSource && <span className="cc-band-empty">Kein Audiokanal (Ansicht ohne Steuerung)</span>}
      </div>
      <dl className="cc-band-values" aria-live="off">
        <div><dt>MIC</dt><dd className="mono">{micOff ? "aus" : numbers?.relative ? `${Math.round(numbers.relative.input * 100)} %` : `${fmtDb(numbers?.input?.peak)} dBFS`}</dd></div>
        <div><dt>JARVIS</dt><dd className="mono">{numbers?.relative ? `${Math.round(numbers.relative.output * 100)} %` : numbers?.output ? `${fmtDb(numbers.output.peak)} dBFS` : "–"}{numbers?.muted && <em> · stumm</em>}</dd></div>
        {numbers?.relative && <div><dd className="cc-hint">Pegel laut ElevenLabs-Client</dd></div>}
      </dl>
    </section>
  );
}

/** Kleiner Pegel im aktiven Zustandssegment; liest denselben Messwert (keine eigene Messung). */
export function MiniLevel({ audio, kind, reduced, level }: {
  audio: AudioEngine | null; kind: "input" | "output"; reduced: boolean; level?: (() => number) | null;
}) {
  const [db, setDb] = useState<number | null>(null);
  const levelRef = useRef(level);
  levelRef.current = level;
  useEffect(() => {
    if (!audio && !levelRef.current) return;
    const timer = window.setInterval(() => {
      if (levelRef.current) return setDb(-60 + 60 * Math.max(0, Math.min(1, levelRef.current())));
      if (!audio) return;
      const value = kind === "input" ? audio.levels().input : audio.levels().output;
      setDb(value > 0 ? dbfs(value / (kind === "input" ? 6 : 4)) : -60);
    }, reduced ? 500 : 100);
    return () => window.clearInterval(timer);
  }, [audio, kind, reduced, !!level]);
  if (db == null) return null;
  return (
    <span className="cc-mini" aria-hidden="true">
      <i style={{ width: `${Math.round(((db + 60) / 60) * 100)}%`, background: kind === "input" ? MIC : VOICE }} />
    </span>
  );
}
