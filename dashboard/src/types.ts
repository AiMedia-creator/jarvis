export type Role = "owner" | "observer";
export type Status =
  "idle" | "listening" | "transcribing" | "thinking" | "speaking";
export type City = {
  id: string;
  name: string;
  country: string;
  lat: number;
  lon: number;
};
export type Camera = {
  id: string;
  name: string;
  city_id: string;
  kind: "mock" | "mp4" | "hls";
  url?: string;
};
export type Snapshot = {
  demo: boolean;
  status: Status;
  stream_id: string | null;
  view: { city_id?: string; lat: number; lon: number; height: number } | null;
  selected_camera: string | null;
  transcripts: Transcript[];
  logs: Log[];
  metrics: Metrics;
};
export type Transcript = {
  id: string;
  role: "user" | "assistant";
  text: string;
  stream_id: string | null;
  final: boolean;
};
export type Log = {
  id: string;
  time: string | number;
  level: string;
  message: string;
};
export type Metrics = {
  uptime_s: number | null;
  event_loop_lag_ms: number | null;
  rooms: number | null;
  clients: number | null;
  interrupts: number | null;
  stop_ack_ms: number | null;
  stt_ms: number | null;
  tts_ms: number | null;
  dropped_messages: number | null;
  jvm_heap_used_mb?: number | null;
  jvm_threads?: number | null;
};
export type Config = {
  demo: boolean;
  auth_required: boolean;
  cities: City[];
  cameras: Camera[];
};
export type ServerEvent = { type: string; [key: string]: any };
