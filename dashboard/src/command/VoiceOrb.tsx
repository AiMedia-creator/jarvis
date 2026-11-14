import { useEffect, useRef } from "react";

/** Zustände des Sprachkreises. „gesperrt“ = der Browser verweigert das Mikrofon. */
export type OrbState = "bereit" | "hoert" | "verarbeitet" | "antwortet" | "fehler" | "gesperrt" | "offline";

const TITLE: Record<OrbState, string> = {
  bereit: "Bereit", hoert: "Hört zu", verarbeitet: "Verarbeitet", antwortet: "Antwortet", fehler: "Fehler",
  gesperrt: "Mikrofon gesperrt", offline: "Offline",
};
const HINT: Record<OrbState, string> = {
  bereit: "Tippe auf den Kreis oder drück M, um zu sprechen – oder schreib unten.",
  hoert: "Ich höre zu, Master. Sprich einfach.",
  verarbeitet: "Einen Moment, Master.",
  antwortet: "Esc unterbricht die Antwort.",
  fehler: "Etwas ist schiefgegangen. Die Meldung steht oben; schreiben geht weiterhin.",
  gesperrt: "Der Browser blockiert das Mikrofon. Erlaube es über das Schloss-Symbol in der Adressleiste – schreiben geht weiterhin.",
  offline: "Keine Verbindung zu Jarvis. Die Neuverbindung läuft.",
};

/**
 * Kreisförmiger Sprachassistent. Der Kern folgt dem echten Pegel (Eingang beim Zuhören, Ausgang beim Antworten),
 * direkt am Element und ohne React-Render pro Bild. Bei „Bewegung reduzieren“ steht alles still; der Zustand ist
 * dann allein über Text und Form erkennbar. Der Kreis ist ein Schalter für das Mikrofon.
 */
export function VoiceOrb({ state, micActive, level, onToggle, disabled, reduced, toggleLabel, title, hint }: {
  state: OrbState;
  /** Überschreibt Titel bzw. Hinweis des Zustands (z. B. beim Verbindungsaufbau). */
  title?: string;
  hint?: string;
  /** true nur, wenn das Mikrofon tatsächlich offen ist. */
  micActive: boolean;
  /** Aktueller Pegel 0–1 (Eingang oder Ausgang, je nach Zustand). */
  level: () => number;
  onToggle: () => void;
  disabled?: boolean;
  reduced: boolean;
  toggleLabel: string;
}) {
  const core = useRef<HTMLSpanElement>(null);
  const live = useRef({ state, level });
  live.current = { state, level };
  useEffect(() => {
    if (reduced) { core.current?.style.setProperty("--orb-level", "0"); return; }
    let frame = 0;
    let smooth = 0;
    const tick = () => {
      frame = requestAnimationFrame(tick);
      if (document.hidden) return;
      const { state: st, level: read } = live.current;
      const raw = st === "hoert" || st === "antwortet" ? Math.max(0, Math.min(1, read() || 0)) : 0;
      smooth += (raw - smooth) * (raw > smooth ? 0.35 : 0.1);
      core.current?.style.setProperty("--orb-level", smooth.toFixed(3));
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [reduced]);

  return (
    <div className={`orb-wrap ${state}`} data-state={state}>
      <button type="button" className="orb" onClick={onToggle} disabled={disabled} aria-pressed={micActive} aria-label={toggleLabel}>
        <span className="orb-ring outer" aria-hidden="true" />
        <span className="orb-ring inner" aria-hidden="true" />
        <span className="orb-core" ref={core} aria-hidden="true">
          {state === "fehler" ? "!" : state === "gesperrt" || state === "offline" ? (
            <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zM6 11a6 6 0 0 0 12 0M12 17v4M4 4l16 16" /></svg>
          ) : (
            <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"><path d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zM6 11a6 6 0 0 0 12 0M12 17v4" /></svg>
          )}
        </span>
      </button>
      <div className="orb-text" role="status" aria-live="polite">
        <strong>{title ?? TITLE[state]}</strong>
        <span className={`orb-mic ${micActive ? "on" : ""}`}>{micActive ? "Mikrofon aktiv" : "Mikrofon aus"}</span>
        <p>{hint ?? HINT[state]}</p>
      </div>
    </div>
  );
}
