import { useCallback, useEffect, useRef, useState } from "react";
import type { AudioEngine } from "../audio";
import type { ContentView } from "../view/intent";
import type { Role } from "../types";
import { VoiceControls } from "./VoiceControls";

const VIEWS: { id: ContentView; label: string }[] = [
  { id: "conversation", label: "Gespräch" },
  { id: "command", label: "Kommandozentrale" },
  { id: "world", label: "Weltansicht" },
  { id: "news", label: "Nachrichten" },
  { id: "market", label: "Märkte" },
  { id: "cameras", label: "Kameras" },
  { id: "memory", label: "Gedächtnis" },
  { id: "ops", label: "System" },
];
const IDLE_MS = 3200;

/**
 * Bedienleiste: erscheint bei Maus-/Touch-Bewegung, Tastaturfokus oder über den Menüknopf und
 * verschwindet im ungestörten Gespräch. Bleibt im DOM und damit per Tab erreichbar.
 */
export function Dock({
  audio,
  role,
  micOn,
  onMicChange,
  onLog,
  text,
  onText,
  onSend,
  onStop,
  view,
  onView,
  onOpenSecond,
  alert,
}: {
  audio: AudioEngine | null;
  role: Role;
  micOn: boolean;
  onMicChange: (on: boolean) => void;
  onLog: (message: string, level?: "info" | "warn") => void;
  text: string;
  onText: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  view: ContentView;
  onView: (view: ContentView) => void;
  onOpenSecond: () => void;
  alert: string | null;
}) {
  const [revealed, setRevealed] = useState(false);
  const [menu, setMenu] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const timer = useRef<number | undefined>(undefined);

  const reveal = useCallback(() => {
    setRevealed(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const root = rootRef.current;
      // Nicht verstecken, solange man darin arbeitet.
      if (root && (root.contains(document.activeElement) || root.matches(":hover"))) return reveal();
      setRevealed(false);
      setMenu(false);
    }, IDLE_MS);
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Tab" || event.key === "/") reveal();
      if (event.key === "/" && document.activeElement === document.body) {
        event.preventDefault();
        rootRef.current?.querySelector<HTMLInputElement>("input")?.focus();
      }
    };
    window.addEventListener("pointermove", reveal, { passive: true });
    window.addEventListener("touchstart", reveal, { passive: true });
    window.addEventListener("pointerdown", reveal, { passive: true });
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointermove", reveal);
      window.removeEventListener("touchstart", reveal);
      window.removeEventListener("pointerdown", reveal);
      window.removeEventListener("keydown", onKey);
      window.clearTimeout(timer.current);
    };
  }, [reveal]);

  const visible = revealed || menu || !!alert || text.length > 0;
  return (
    <div
      ref={rootRef}
      className={`dock ${visible ? "revealed" : ""}`}
      onFocusCapture={reveal}
      role="toolbar"
      aria-label="Jarvis-Bedienung"
    >
      {alert && (
        <div className="dock-alert" role="status">
          {alert}
        </div>
      )}
      <div className="dock-bar">
        <VoiceControls audio={audio} role={role} micOn={micOn} onMicChange={onMicChange} onLog={onLog} />
        <form
          className="dock-input"
          onSubmit={(event) => {
            event.preventDefault();
            onSend();
          }}
        >
          <input
            value={text}
            maxLength={8000}
            placeholder={role === "owner" ? "Schreib Jarvis …" : "Nur Ansicht (Observer)"}
            aria-label="Nachricht an Jarvis"
            disabled={role !== "owner"}
            onChange={(event) => onText(event.target.value)}
          />
          <button type="submit" className="dock-send" aria-label="Senden" disabled={role !== "owner" || !text.trim()}>
            ↵
          </button>
        </form>
        <button type="button" className="dock-stop" onClick={onStop} aria-label="Ausgabe stoppen (Esc)" title="Stopp (Esc)">
          ■
        </button>
        <button
          type="button"
          className={`dock-menu ${menu ? "active" : ""}`}
          aria-haspopup="menu"
          aria-expanded={menu}
          aria-label="Ansichten und Einstellungen"
          onClick={() => {
            setMenu((open) => !open);
            reveal();
          }}
        >
          ☰
        </button>
      </div>
      {menu && (
        <nav className="dock-popover" role="menu" aria-label="Ansichten">
          {VIEWS.map((item) => (
            <button
              type="button"
              role="menuitemradio"
              aria-checked={view === item.id}
              key={item.id}
              className={view === item.id ? "active" : ""}
              onClick={() => {
                onView(item.id);
                setMenu(false);
              }}
            >
              {item.label}
            </button>
          ))}
          <hr />
          <button type="button" role="menuitem" onClick={onOpenSecond}>
            Operations auf Screen 2 ↗
          </button>
        </nav>
      )}
    </div>
  );
}
