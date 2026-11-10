import Hls from "hls.js";
import type { Camera } from "./types";
export class CctvPanel {
  private canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private raf = 0;
  private video: HTMLVideoElement | null = null;
  private hls: Hls | null = null;
  private observer: IntersectionObserver;
  private camera: Camera | null = null;
  constructor(root: HTMLElement) {
    this.canvas = document.createElement("canvas");
    this.canvas.width = 800;
    this.canvas.height = 450;
    this.canvas.className = "cctv-canvas";
    root.append(this.canvas);
    this.ctx = this.canvas.getContext("2d")!;
    this.observer = new IntersectionObserver(
      (es) => {
        if (es[0]?.isIntersecting) this.start();
        else this.stop();
      },
      { threshold: 0.1 },
    );
    this.observer.observe(this.canvas);
  }
  select(camera: Camera | null) {
    this.destroyMedia();
    this.camera = camera;
    if (camera?.kind !== "mock" && camera?.url) {
      this.video = document.createElement("video");
      this.video.muted = true;
      this.video.autoplay = true;
      this.video.playsInline = true;
      this.video.controls = false;
      this.canvas.replaceWith(this.video);
      this.observer.disconnect();
      if (
        camera.kind === "hls" &&
        !this.video.canPlayType("application/vnd.apple.mpegurl") &&
        Hls.isSupported()
      ) {
        this.hls = new Hls({ maxBufferLength: 12 });
        this.hls.loadSource(camera.url);
        this.hls.attachMedia(this.video);
      } else this.video.src = camera.url;
      this.video.addEventListener("error", () =>
        this.drawFallback("QUELLE NICHT VERFÜGBAR"),
      );
      this.video.play().catch(() => {});
    } else {
      this.canvas = document.createElement("canvas");
      this.canvas.width = 800;
      this.canvas.height = 450;
      this.canvas.className = "cctv-canvas";
      this.video?.replaceWith(this.canvas);
      this.ctx = this.canvas.getContext("2d")!;
      this.observer.observe(this.canvas);
      this.start();
    }
  }
  private start() {
    if (this.video) return;
    cancelAnimationFrame(this.raf);
    const loop = (t: number) => {
      this.draw(t);
      this.raf = requestAnimationFrame(loop);
    };
    this.raf = requestAnimationFrame(loop);
  }
  private stop() {
    cancelAnimationFrame(this.raf);
  }
  private draw(t: number) {
    const c = this.ctx,
      w = this.canvas.width,
      h = this.canvas.height;
    c.fillStyle = "#000000";
    c.fillRect(0, 0, w, h);
    c.strokeStyle = "rgba(255,149,0,.08)";
    for (let x = 0; x < w; x += 40) {
      c.beginPath();
      c.moveTo(x, 0);
      c.lineTo(x, h);
      c.stroke();
    }
    for (let y = 0; y < h; y += 40) {
      c.beginPath();
      c.moveTo(0, y);
      c.lineTo(w, y);
      c.stroke();
    }
    const x = 120 + Math.sin(t / 1300) * 90;
    c.fillStyle = "#ef4444";
    c.globalAlpha = 0.1;
    c.fillRect(x, 90, 220, 230);
    c.globalAlpha = 1;
    c.strokeStyle = "#ff9500";
    c.strokeRect(70, 70, 270, 250);
    c.fillStyle = "#ff9500";
    c.font = "bold 14px \"JetBrains Mono\", monospace";
    c.fillText("SIMULATION // MOCK", 24, 30);
    c.fillText(new Date().toISOString().slice(11, 19) + "Z", w - 160, 30);
    c.fillStyle = "#f4ede2";
    c.fillText(this.camera?.name ?? "KEINE KAMERA", 24, h - 24);
  }
  private drawFallback(msg: string) {
    this.ctx = this.canvas.getContext("2d")!;
    this.ctx.fillStyle = "#000000";
    this.ctx.fillRect(0, 0, 800, 450);
    this.ctx.fillStyle = "#ff9500";
    this.ctx.font = "16px \"JetBrains Mono\", monospace";
    this.ctx.fillText(msg, 40, 80);
  }
  private destroyMedia() {
    this.stop();
    this.hls?.destroy();
    this.hls = null;
    if (this.video) {
      this.video.pause();
      this.video.removeAttribute("src");
      this.video.load();
      this.video.remove();
      this.video = null;
    }
    if (this.canvas && this.canvas.isConnected) this.canvas.remove();
  }
  destroy() {
    this.destroyMedia();
    this.observer.disconnect();
  }
}
