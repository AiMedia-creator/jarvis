import { OpsMetricsPanel } from "../components/OpsMetricsPanel";
import { MemoryPanel } from "../components/MemoryPanel";
import { StatusTag } from "../components/HudChrome";
import { ProtocolPanel } from "../components/ProtocolPanel";
import type { Log, Metrics, Status, Transcript } from "../types";
import { ViewFrame } from "./ViewFrame";

const logTime = (value: string | number) => {
  const date = new Date(typeof value === "number" ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? "—" : `${date.toISOString().slice(11, 19)}Z`;
};

export function TranscriptList({ items }: { items: Transcript[] }) {
  return (
    <div className="scroll-list">
      {items.length === 0 && <div className="empty">NO TRANSCRIPT EVENTS</div>}
      {items.map((item) => (
        <div className={`transcript ${item.role}`} key={item.id}>
          <div>
            <b>{item.role === "user" ? "INPUT" : "JARVIS"}</b>
            <span>{item.final ? "FINAL" : "LIVE"}</span>
          </div>
          <p>{item.text}</p>
        </div>
      ))}
    </div>
  );
}

export function LogList({ items }: { items: Log[] }) {
  return (
    <div className="scroll-list logs">
      {items.length === 0 && <div className="empty">NO SYSTEM EVENTS</div>}
      {items.map((item) => (
        <div className={item.level === "warn" ? "warn" : ""} key={item.id}>
          {logTime(item.time)} · {item.message}
        </div>
      ))}
    </div>
  );
}

/** Technische Operationsansicht: nur bewusst aufgerufen (Menü, "Zeig mir die System…" oder Screen 2). */
export default function OpsView({
  metrics,
  transcripts,
  logs,
  status,
  room,
  onBack,
  onOpenSecond,
}: {
  metrics: Metrics;
  transcripts: Transcript[];
  logs: Log[];
  status: Status;
  room: string;
  onBack?: () => void;
  onOpenSecond?: () => void;
}) {
  const body = (
    <>
      <div className="ops-links">
        <StatusTag tone="data">{status.toUpperCase()}</StatusTag>
        <span className="pill">ROOM {room}</span>
        {onOpenSecond && (
          <button type="button" className="btn" onClick={onOpenSecond}>
            Auf Screen 2 öffnen ↗
          </button>
        )}
      </div>
      <div className="ops-columns">
        <section className="panel ops-panel">
          <div className="eyebrow">JVM / PIPELINE</div>
          <OpsMetricsPanel metrics={metrics} />
          <div className="eyebrow section-gap">TRANSCRIPT STREAM</div>
          <TranscriptList items={transcripts} />
        </section>
        <section className="panel ops-panel">
          <div className="eyebrow">ÖFFENTLICHE EREIGNISSE · WERKZEUGE · PROTOKOLL</div>
          <LogList items={logs} />
          <div className="protocol-note">
            <b>THOUGHT STREAMS</b>
            <p>Nur sichtbare Ereignisse und Antworten – keine verborgenen Modell-Gedanken.</p>
          </div>
        </section>
      </div>
      <ProtocolPanel />
      <MemoryPanel room={room} />
    </>
  );
  if (!onBack) return <main className="ops-main">{body}</main>;
  return (
    <ViewFrame className="ops-view" eyebrow="System" title="Operations" onBack={onBack}>
      {body}
    </ViewFrame>
  );
}
