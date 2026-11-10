import { useEffect, useRef } from "react";
import { api, type BusEvent } from "./api";

export type CameraInfo = {
  art?: string; video?: string; url?: string; titel?: string; stadt?: string; live?: boolean; quelle?: string; hinweis?: string; link?: string;
};

/** Long-Polling auf den Ereignis-Bus (Kameras, Jobs) über /api/events; bricht beim Verlassen sauber ab. */
export function useBusEvents(enabled: boolean, onEvent: (event: BusEvent) => void) {
  const handler = useRef(onEvent);
  handler.current = onEvent;
  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    let stopped = false;
    const loop = async () => {
      let seq = 0;
      try { seq = (await api.events(0, 0, controller.signal)).seq; } catch { /* Server noch nicht da */ }
      while (!stopped) {
        try {
          const result = await api.events(seq, 15, controller.signal);
          // Neustart des Servers: Zähler beginnt wieder bei 0.
          seq = result.seq < seq ? 0 : result.seq;
          for (const event of result.events) handler.current(event);
        } catch {
          if (stopped) return;
          await new Promise((resolve) => window.setTimeout(resolve, 5000));
        }
      }
    };
    void loop();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [enabled]);
}

const WINDY = /^https:\/\/([a-z0-9-]+\.)*windy\.com\//;

/** Live-Kamera wie früher auf 8765: YouTube (ohne Cookies), Windy-Player oder Windy-Standbild. */
export function cameraSource(k: CameraInfo): { kind: "iframe" | "img"; src: string } | null {
  if (k.art === "youtube" && /^[\w-]{11}$/.test(k.video || ""))
    return { kind: "iframe", src: `https://www.youtube-nocookie.com/embed/${k.video}?autoplay=1&mute=1&playsinline=1&rel=0&modestbranding=1` };
  if (k.art === "windy" && WINDY.test(k.url || "")) return { kind: "iframe", src: k.url! };
  if (k.art === "bild" && WINDY.test(k.url || "")) return { kind: "img", src: k.url! };
  return null;
}

export function LiveCamera({ camera, onClose }: { camera: CameraInfo; onClose: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { e.stopImmediatePropagation(); onClose(); } };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  const source = cameraSource(camera);
  const title = `${camera.stadt ? `${camera.stadt} – ` : ""}${camera.titel ?? "Live-Kamera"}`;
  return (
    <div className="cc-dialog-bg cc-camera-bg" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="cc-camera" role="dialog" aria-modal="true" aria-label={title}>
        <header>
          <span className={`cc-live ${camera.live ? "" : "off"}`}>{camera.live ? "LIVE" : camera.art === "bild" ? "BILD" : "ZEITRAFFER"}</span>
          <h2>{title}</h2>
          {/^https:\/\//.test(camera.link || "") && <a href={camera.link} target="_blank" rel="noopener noreferrer" className="cc-link">Quelle öffnen ↗</a>}
          <button ref={closeRef} type="button" className="cc-btn small" onClick={onClose}>Schließen <kbd>Esc</kbd></button>
        </header>
        <div className="cc-camera-frame">
          {source?.kind === "iframe" && (
            <iframe src={source.src} title={title} allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen
              referrerPolicy="strict-origin-when-cross-origin" />
          )}
          {source?.kind === "img" && <img src={source.src} alt={camera.titel || "Webcam"} />}
          {!source && <p className="cc-empty">Diese Kameraquelle wird aus Sicherheitsgründen nicht eingebettet.</p>}
        </div>
        {(camera.quelle || camera.hinweis) && <p className="cc-hint">{[camera.quelle, camera.hinweis].filter(Boolean).join(" · ")}</p>}
      </div>
    </div>
  );
}
