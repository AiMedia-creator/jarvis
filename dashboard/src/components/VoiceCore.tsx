import { useEffect, useRef } from "react";
import type { Status } from "../types";

type Levels = () => { input: number; output: number };

type Props = {
  status: Status;
  /** Zeitpunkt (performance.now) der letzten Unterbrechung: Bewegung beruhigt sich sofort. */
  interruptedAt: number;
  levels: Levels;
  /** Kleiner Präsenzpunkt, während eine Inhaltsansicht offen ist. */
  compact: boolean;
  onActivate?: () => void;
};

/** Zielwerte je Zustand: Atmen, Reaktion auf Pegel, innere Bewegung, Helligkeit. */
const MOOD: Record<Status | "interrupted", { breathe: number; react: number; swirl: number; glow: number; hz: number }> = {
  idle: { breathe: 0.018, react: 0, swirl: 0, glow: 0.55, hz: 0.16 },
  listening: { breathe: 0.01, react: 0.22, swirl: 0, glow: 0.7, hz: 0.3 },
  transcribing: { breathe: 0.012, react: 0.12, swirl: 0.35, glow: 0.72, hz: 0.35 },
  thinking: { breathe: 0.02, react: 0, swirl: 1, glow: 0.78, hz: 0.45 },
  speaking: { breathe: 0.01, react: 0.3, swirl: 0.15, glow: 0.9, hz: 0.3 },
  interrupted: { breathe: 0.004, react: 0, swirl: 0, glow: 0.6, hz: 0.1 },
};

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/**
 * Organischer Voice-Core: eine warme, orangefarbene Lichtblase. Liest die Pegel pro Frame direkt aus der
 * AudioEngine (keine React-Renders pro Frame) und folgt den vorhandenen Sprachzuständen.
 */
export function VoiceCore({ status, interruptedAt, levels, compact, onActivate }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const live = useRef({ status, interruptedAt, levels });
  live.current = { status, interruptedAt, levels };

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const g = canvas.getContext("2d");
    if (!g) return;
    const reduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    let raf = 0;
    let width = 0;
    let height = 0;
    let dpr = 1;
    const s = { breathe: 0.018, react: 0, swirl: 0, glow: 0.55, hz: 0.16, level: 0 };
    const phases = [0.3, 1.7, 2.9, 4.1, 5.3];

    const resize = () => {
      dpr = Math.min(2, window.devicePixelRatio || 1);
      const rect = canvas.getBoundingClientRect();
      width = Math.max(1, Math.round(rect.width * dpr));
      height = Math.max(1, Math.round(rect.height * dpr));
      canvas.width = width;
      canvas.height = height;
    };
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    resize();

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      if (document.hidden) return;
      const t = now / 1000;
      const { status: st, interruptedAt: cut, levels: read } = live.current;
      const calming = now - cut < 700;
      const target = MOOD[calming ? "interrupted" : st];
      const k = calming ? 0.25 : 0.06;
      s.breathe = lerp(s.breathe, target.breathe, k);
      s.react = lerp(s.react, target.react, k);
      s.swirl = lerp(s.swirl, target.swirl, 0.04);
      s.glow = lerp(s.glow, target.glow, 0.05);
      s.hz = lerp(s.hz, target.hz, 0.05);
      const { input, output } = read();
      const raw = st === "speaking" ? output : st === "listening" || st === "transcribing" ? input : 0;
      s.level = lerp(s.level, calming ? 0 : raw, raw > s.level ? 0.35 : 0.12);

      const motion = reduced ? 0.25 : 1;
      const cx = width / 2;
      const cy = height / 2;
      const base = Math.min(width, height) * 0.27;
      const pulse = 1 + Math.sin(t * Math.PI * 2 * s.hz) * s.breathe * motion + s.level * s.react * 0.6;
      const radius = base * pulse;

      g.clearRect(0, 0, width, height);
      // Weiter, weicher Schein
      // Schein endet sicher innerhalb der Fläche: keine sichtbare Kante.
      const haloEnd = Math.min(radius * 2.4, Math.min(width, height) / 2 - 2);
      const halo = g.createRadialGradient(cx, cy, radius * 0.4, cx, cy, haloEnd);
      halo.addColorStop(0, `rgba(255,149,0,${0.22 * s.glow})`);
      halo.addColorStop(0.45, `rgba(255,110,0,${0.08 * s.glow})`);
      halo.addColorStop(1, "rgba(255,90,0,0)");
      g.fillStyle = halo;
      g.fillRect(0, 0, width, height);

      // Organische Kontur: wenige überlagerte Wellen, Amplitude folgt dem Pegel
      const wobble = (0.018 + s.level * s.react * 0.5) * motion;
      g.beginPath();
      const steps = 120;
      for (let i = 0; i <= steps; i++) {
        const a = (i / steps) * Math.PI * 2;
        let r = 1;
        for (let n = 0; n < phases.length; n++) {
          const harmonic = n + 2;
          r += (Math.sin(a * harmonic + t * (0.35 + n * 0.17) * motion + phases[n]) * wobble) / (n + 1);
        }
        const x = cx + Math.cos(a) * radius * r;
        const y = cy + Math.sin(a) * radius * r;
        if (i === 0) g.moveTo(x, y);
        else g.lineTo(x, y);
      }
      g.closePath();
      const body = g.createRadialGradient(cx - radius * 0.25, cy - radius * 0.3, radius * 0.05, cx, cy, radius * 1.05);
      body.addColorStop(0, `rgba(255,226,170,${0.55 + 0.45 * s.glow})`);
      body.addColorStop(0.3, "rgba(255,176,60,0.97)");
      body.addColorStop(0.7, "rgba(255,140,0,0.85)");
      body.addColorStop(1, "rgba(220,80,0,0.15)");
      g.fillStyle = body;
      g.shadowColor = "rgba(255,149,0,0.6)";
      g.shadowBlur = 40 * dpr * s.glow;
      g.fill();
      g.shadowBlur = 0;

      // Innere, kontrollierte Bewegung beim Verarbeiten (kein Flackern)
      if (s.swirl > 0.02) {
        g.save();
        g.clip();
        g.globalCompositeOperation = "lighter";
        for (let n = 0; n < 3; n++) {
          const angle = t * 0.6 * motion + (n * Math.PI * 2) / 3;
          const ox = cx + Math.cos(angle) * radius * 0.35;
          const oy = cy + Math.sin(angle) * radius * 0.35;
          const glow = g.createRadialGradient(ox, oy, 0, ox, oy, radius * 0.7);
          glow.addColorStop(0, `rgba(255,236,200,${0.22 * s.swirl})`);
          glow.addColorStop(1, "rgba(255,180,80,0)");
          g.fillStyle = glow;
          g.fillRect(cx - radius * 1.5, cy - radius * 1.5, radius * 3, radius * 3);
        }
        g.restore();
      }
    };
    raf = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(raf);
      observer.disconnect();
    };
  }, []);

  const label = {
    idle: "Bereit",
    listening: "Hört zu",
    transcribing: "Erkennt Sprache",
    thinking: "Verarbeitet",
    speaking: "Spricht",
  }[status];
  return (
    <div className={`voice-core ${compact ? "compact" : ""}`} data-state={status}>
      <canvas
        ref={canvasRef}
        role="img"
        aria-label={`Jarvis: ${label}`}
        onClick={onActivate}
      />
      <span className="sr-only" aria-live="polite">
        {label}
      </span>
    </div>
  );
}
