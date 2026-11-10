import type { Metrics } from "../types";

const metric = (value: number | null | undefined, suffix = "") =>
  value == null ? "—" : `${Math.round(value)}${suffix}`;
const cells: [keyof Metrics, string, string][] = [
  ["uptime_s", "UPTIME", "s"],
  ["event_loop_lag_ms", "LOOP LAG", "ms"],
  ["clients", "CLIENTS", ""],
  ["rooms", "ROOMS", ""],
  ["interrupts", "INTERRUPTS", ""],
  ["stop_ack_ms", "STOP ACK", "ms"],
  ["stt_ms", "STT", "ms"],
  ["tts_ms", "TTS", "ms"],
  ["dropped_messages", "DROPPED", ""],
  ["jvm_heap_used_mb", "JVM HEAP USED", " MB"],
  ["jvm_threads", "JVM THREADS", ""],
];
export function OpsMetricsPanel({ metrics }: { metrics: Metrics }) {
  return (
    <div className="metrics">
      {cells.map(([key, label, suffix]) => (
        <div className="metric" key={key}>
          <strong>{metric(metrics[key], suffix)}</strong>
          <span>{label}</span>
        </div>
      ))}
    </div>
  );
}
