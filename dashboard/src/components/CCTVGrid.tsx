import Hls from "hls.js";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Camera } from "../types";

const subscribers = new Set<() => void>();
let clockTimer: number | undefined;
let clockValue = new Date();
function tickClock() {
  clockValue = new Date();
  subscribers.forEach((listener) => listener());
}
function subscribeClock(listener: () => void) {
  subscribers.add(listener);
  if (subscribers.size === 1) clockTimer = window.setInterval(tickClock, 1000);
  return () => {
    subscribers.delete(listener);
    if (!subscribers.size && clockTimer !== undefined) {
      window.clearInterval(clockTimer);
      clockTimer = undefined;
    }
  };
}
function useUtcClock() {
  const [, render] = useState(0);
  useEffect(() => subscribeClock(() => render((value) => value + 1)), []);
  return clockValue.toISOString().slice(11, 19);
}

export function pageSizeForLayout(layout: "2x2" | "3x3"): number {
  return layout === "2x2" ? 4 : 9;
}
export function visibleCameras(
  cameras: Camera[],
  layout: "2x2" | "3x3",
  page: number,
): Camera[] {
  const size = pageSizeForLayout(layout);
  const start = Math.max(0, page) * size;
  return cameras.slice(start, start + size);
}

type Ptz = { pan: number; tilt: number; zoom: number };
function FeedMedia({
  camera,
  index,
  ptz,
}: {
  camera: Camera;
  index: number;
  ptz: Ptz;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const activeRef = useRef(true);
  const intersectingRef = useRef(true);
  const utc = useUtcClock();
  const utcRef = useRef(utc);
  utcRef.current = utc;
  const [sourceState, setSourceState] = useState<
    "loading" | "live" | "unavailable"
  >("loading");
  const [message, setMessage] = useState("VERBINDUNG WIRD GEPRÜFT");
  const [active, setActive] = useState(true);
  activeRef.current = active;
  const mediaTransform = `translate(${ptz.pan * -10}%, ${ptz.tilt * -10}%) scale(${ptz.zoom})`;

  useEffect(() => {
    if (camera.kind === "mock") {
      const canvas = canvasRef.current;
      const context = canvas?.getContext("2d");
      if (!canvas || !context) return;
      const width = 640;
      const height = 360;
      canvas.width = width;
      canvas.height = height;
      let frame = 0;
      let lastDraw = 0;
      const draw = (time: number) => {
        if (activeRef.current && time - lastDraw >= 1000 / 8) {
          lastDraw = time;
          context.fillStyle = "#000000";
          context.fillRect(0, 0, width, height);
          context.strokeStyle = "rgba(255,149,0,.07)";
          context.lineWidth = 1;
          for (let x = 0; x < width; x += 32) {
            context.beginPath();
            context.moveTo(x, 0);
            context.lineTo(x, height);
            context.stroke();
          }
          for (let y = 0; y < height; y += 32) {
            context.beginPath();
            context.moveTo(0, y);
            context.lineTo(width, y);
            context.stroke();
          }
          const px = 160 + Math.sin(time / 950 + index) * 125;
          const py = 120 + Math.cos(time / 1200 + index) * 38;
          context.fillStyle = "rgba(239,68,68,.12)";
          context.fillRect(px, py, 220, 150);
          context.strokeStyle = "#ff9500";
          context.lineWidth = 2;
          context.strokeRect(px - 12, py - 12, 244, 174);
          context.fillStyle = "#ef4444";
          context.font = "600 16px JetBrains Mono";
          context.fillText("SIMULATION // MOCK", 18, 28);
          context.fillStyle = "#ff9500";
          context.fillText(`${utcRef.current}Z`, width - 150, 28);
          context.fillStyle = "#c9bfb0";
          context.font = "12px JetBrains Mono";
          context.fillText(camera.name.toUpperCase(), 18, height - 17);
          context.fillStyle = "rgba(255,255,255,.055)";
          for (let y = (time / 12) % 8; y < height; y += 8)
            context.fillRect(0, y, width, 1);
        }
        frame = requestAnimationFrame(draw);
      };
      frame = requestAnimationFrame(draw);
      setSourceState("live");
      setMessage("SIMULIERT / LOKALE CANVAS-QUELLE");
      return () => cancelAnimationFrame(frame);
    }

    const video = videoRef.current;
    if (!video || !camera.url) {
      setSourceState("unavailable");
      setMessage("QUELLE NICHT KONFIGURIERT");
      return;
    }
    let hls: Hls | null = null;
    let disposed = false;
    const fail = (text: string) => {
      if (!disposed) {
        setSourceState("unavailable");
        setMessage(text);
      }
    };
    const markLive = () => {
      if (!disposed) {
        setSourceState("live");
        setMessage(camera.kind === "hls" ? "HLS / LIVE" : "MP4 / LIVE");
      }
    };
    const onVideoError = () => fail("QUELLE NICHT VERFÜGBAR");
    video.muted = true;
    video.autoplay = true;
    video.playsInline = true;
    video.addEventListener("playing", markLive);
    video.addEventListener("canplay", markLive);
    video.addEventListener("error", onVideoError);
    if (camera.kind === "hls") {
      const native = video.canPlayType("application/vnd.apple.mpegurl");
      if (native) video.src = camera.url;
      else if (Hls.isSupported()) {
        hls = new Hls({ maxBufferLength: 12 });
        hls.on(Hls.Events.ERROR, (_event, data) => {
          if (data.fatal)
            fail(`HLS-FEHLER: ${data.details ?? "QUELLE NICHT VERFÜGBAR"}`);
        });
        hls.loadSource(camera.url);
        hls.attachMedia(video);
      } else fail("HLS NICHT UNTERSTÜTZT");
    } else video.src = camera.url;
    const play = () => {
      if (!document.hidden && activeRef.current)
        void video.play().catch(() => fail("WIEDERGABE NICHT MÖGLICH"));
    };
    const pause = () => video.pause();
    const onVisibility = () => (document.hidden ? pause() : play());
    document.addEventListener("visibilitychange", onVisibility);
    play();
    return () => {
      disposed = true;
      document.removeEventListener("visibilitychange", onVisibility);
      video.removeEventListener("playing", markLive);
      video.removeEventListener("canplay", markLive);
      video.removeEventListener("error", onVideoError);
      pause();
      hls?.destroy();
      hls = null;
      video.removeAttribute("src");
      video.load();
    };
  }, [camera, index]);

  useEffect(() => {
    const video = videoRef.current;
    if (video && camera.kind !== "mock") {
      if (active && !document.hidden) void video.play().catch(() => undefined);
      else video.pause();
    }
  }, [active, camera.kind]);

  useEffect(() => {
    const onVisibility = () =>
      setActive(intersectingRef.current && !document.hidden);
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  useEffect(() => {
    const node = frameRef.current;
    if (!node || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        intersectingRef.current = entries[0]?.isIntersecting ?? true;
        setActive(intersectingRef.current && !document.hidden);
      },
      { threshold: 0.1 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <div className="feed-media" ref={frameRef}>
      <div className="feed-transform" style={{ transform: mediaTransform }}>
        {camera.kind === "mock" ? (
          <canvas ref={canvasRef} />
        ) : (
          <video ref={videoRef} muted playsInline autoPlay />
        )}
      </div>
      {sourceState !== "live" && (
        <div className={`feed-source-state ${sourceState}`}>{message}</div>
      )}
    </div>
  );
}

const defaultPtz: Ptz = { pan: 0, tilt: 0, zoom: 1 };
function FeedCard({
  camera,
  index,
  selected,
  onSelect,
}: {
  camera: Camera;
  index: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const [ptz, setPtz] = useState<Ptz>(defaultPtz);
  const set = (patch: Partial<Ptz>) =>
    setPtz((current) => ({ ...current, ...patch }));
  const mockPtz = camera.kind === "mock";
  return (
    <article
      className={`feed-card${selected ? " selected" : ""}`}
      role="button"
      tabIndex={0}
      aria-label={`Kamera ${camera.name} auswählen`}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (
          event.target instanceof HTMLElement &&
          event.target.closest("button")
        )
          return;
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
    >
      <div className="feed-head">
        <b>{camera.id.toUpperCase()}</b>
        <span>
          {camera.kind === "mock" ? "MOCK" : camera.kind.toUpperCase()}
        </span>
      </div>
      <FeedMedia camera={camera} index={index} ptz={ptz} />
      <div className="feed-foot">
        <span>UTC {useUtcClock()}Z</span>
        <span className="feed-status">
          {camera.kind === "mock" ? "SIMULIERT" : "SOURCE STATUS"}
        </span>
      </div>
      <div
        className="ptz-controls"
        aria-label={`${camera.name} PTZ-Steuerung`}
        onClick={(event) => event.stopPropagation()}
      >
        <span className="ptz-label">
          {mockPtz
            ? "SIMULIERT PTZ"
            : "PTZ: AUTORISIERTER ENDPOINT ERFORDERLICH"}
        </span>
        <button
          type="button"
          disabled={!mockPtz}
          aria-label="Nach oben schwenken"
          onClick={() => set({ tilt: Math.max(-1, ptz.tilt - 0.2) })}
        >
          ▲
        </button>
        <button
          type="button"
          disabled={!mockPtz}
          aria-label="Nach links schwenken"
          onClick={() => set({ pan: Math.max(-1, ptz.pan - 0.2) })}
        >
          ◀
        </button>
        <button
          type="button"
          disabled={!mockPtz}
          aria-label="PTZ zurücksetzen"
          onClick={() => setPtz(defaultPtz)}
        >
          ●
        </button>
        <button
          type="button"
          disabled={!mockPtz}
          aria-label="Nach rechts schwenken"
          onClick={() => set({ pan: Math.min(1, ptz.pan + 0.2) })}
        >
          ▶
        </button>
        <button
          type="button"
          disabled={!mockPtz}
          aria-label="Nach unten schwenken"
          onClick={() => set({ tilt: Math.min(1, ptz.tilt + 0.2) })}
        >
          ▼
        </button>
        <button
          type="button"
          disabled={!mockPtz}
          aria-label="Hineinzoomen"
          onClick={() => set({ zoom: Math.min(2.4, ptz.zoom + 0.2) })}
        >
          ＋
        </button>
        <button
          type="button"
          disabled={!mockPtz}
          aria-label="Herauszoomen"
          onClick={() => set({ zoom: Math.max(1, ptz.zoom - 0.2) })}
        >
          －
        </button>
      </div>
    </article>
  );
}

export function CCTVGrid({
  cameras,
  selected,
  onSelect,
}: {
  cameras: Camera[];
  selected: string | null;
  onSelect: (id: string) => void;
}) {
  const [layout, setLayout] = useState<"2x2" | "3x3">("2x2");
  const [page, setPage] = useState(0);
  const pageSize = pageSizeForLayout(layout);
  const pageCount = Math.max(1, Math.ceil(cameras.length / pageSize));
  const currentPage = Math.min(page, pageCount - 1);
  const visible = useMemo(
    () => visibleCameras(cameras, layout, currentPage),
    [cameras, currentPage, layout],
  );
  useEffect(() => {
    if (!selected) return;
    const index = cameras.findIndex((camera) => camera.id === selected);
    if (index >= 0) setPage(Math.floor(index / pageSize));
  }, [cameras, pageSize, selected]);
  useEffect(() => {
    setPage(0);
  }, [layout]);
  return (
    <div className="cctv-wrap">
      <div className="cctv-toolbar">
        <div
          className="layout-switch"
          role="group"
          aria-label="CCTV Rastergröße"
        >
          <button
            type="button"
            className={layout === "2x2" ? "active" : ""}
            onClick={() => setLayout("2x2")}
          >
            2 × 2
          </button>
          <button
            type="button"
            className={layout === "3x3" ? "active" : ""}
            onClick={() => setLayout("3x3")}
          >
            3 × 3
          </button>
        </div>
        {pageCount > 1 && (
          <div className="grid-pages">
            <button
              type="button"
              aria-label="Vorherige Kameraseite"
              disabled={currentPage === 0}
              onClick={() => setPage((value) => Math.max(0, value - 1))}
            >
              ‹
            </button>
            <span>
              {currentPage + 1} / {pageCount}
            </span>
            <button
              type="button"
              aria-label="Nächste Kameraseite"
              disabled={currentPage >= pageCount - 1}
              onClick={() =>
                setPage((value) => Math.min(pageCount - 1, value + 1))
              }
            >
              ›
            </button>
          </div>
        )}
      </div>
      <div className={`cctv-grid layout-${layout}`}>
        {visible.map((camera, index) => (
          <FeedCard
            key={camera.id}
            camera={camera}
            index={index}
            selected={selected === camera.id}
            onSelect={() => onSelect(camera.id)}
          />
        ))}
      </div>
      {!visible.length && (
        <div className="empty">NO CAMERA FEEDS CONFIGURED</div>
      )}
    </div>
  );
}
