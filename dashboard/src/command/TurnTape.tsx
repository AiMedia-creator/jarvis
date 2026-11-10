import type { Telemetry, Turn } from "./telemetry";

export const fmtMs = (v: number | null | undefined): string => {
  if (v == null || !Number.isFinite(v)) return "–";
  const abs = Math.abs(v);
  if (abs >= 10_000) return `${(v / 1000).toFixed(1).replace(".", ",")} s`;
  return `${Math.round(v)} ms`;
};
const clockOf = (perf: number) => new Date(Date.now() - (performance.now() - perf)).toLocaleTimeString("de-DE");

type Block = { from: number; to: number; kind: "solid" | "light" | "hatch" | "outline"; title: string };
type Track = { key: "mic" | "stt" | "llm" | "tts"; label: string; blocks: Block[]; marks: { at: number; title: string }[]; note?: string };

const STAGE_TRACK: Record<string, Track["key"]> = { stt: "stt", llm: "llm", tts: "tts", audio: "tts", connection: "llm" };

/** Baut die vier Spuren eines Zugs. Server-Blöcke nur mit Uhrabgleich – sonst nur als Dauer im Text. */
export function tracksFor(telemetry: Telemetry, turn: Turn, now: number): { anchor: number; aligned: boolean; end: number; start: number; tracks: Track[] } {
  const L = (v: number | null | undefined) => telemetry.toLocal(v);
  const anchor = turn.t0 ?? L(turn.server.turn_dispatch) ?? turn.createdAt;
  const aligned = turn.t0 != null;
  const frags = telemetry.fragmentsOf(turn);
  const synced = telemetry.offset != null;
  const playEnd = turn.localStop ?? turn.audibleEnd ?? (turn.audibleStart != null ? now : null);
  const genEnd = L(turn.server.generation_done) ?? L(turn.server.stop) ?? (turn.stop ? turn.localStop : null);
  const mic: Track = { key: "mic", label: "MIC", blocks: [], marks: [] };
  const stt: Track = { key: "stt", label: "ERKENNUNG", blocks: [], marks: [] };
  const llm: Track = { key: "llm", label: "CLAUDE", blocks: [], marks: [] };
  const tts: Track = { key: "tts", label: "STIMME", blocks: [], marks: [] };
  if (turn.source === "text") {
    mic.note = "getippt";
    stt.note = "getippt";
  }
  if (turn.source === "realtime") {
    // Echtzeit: Einzelstufen liegen beim Anbieter. Nur Dauern ab Eingang der erkannten Äußerung.
    mic.note = "Echtzeit – Satzende meldet der Anbieter nicht";
    stt.note = "Agent (Erkennung + Claude) beim Anbieter";
    const until = turn.audibleStart ?? turn.localStop ?? now;
    llm.blocks.push({ from: turn.createdAt, to: until, kind: "light", title: `Agent bis Sprechbeginn ${fmtMs(until - turn.createdAt)}` });
    turn.tools.forEach((tool) => llm.marks.push({ at: tool.t, title: `Werkzeug: ${tool.name}` }));
  }
  frags.forEach((f, index) => {
    if (f.vadStart != null && f.voicedEnd != null)
      mic.blocks.push({ from: f.vadStart, to: f.voicedEnd, kind: "solid", title: `Sprechen ${fmtMs(f.voicedEnd - f.vadStart)}` });
    if (f.voicedEnd != null && f.cause === "vad" && f.hangoverMs > 0)
      mic.blocks.push({ from: f.voicedEnd, to: f.voicedEnd + f.hangoverMs, kind: "light", title: `Nachlauf ${fmtMs(f.hangoverMs)}` });
    if (index > 0 && f.vadStart != null) mic.marks.push({ at: f.vadStart, title: `Klebestelle: Teil ${index + 1}` });
    const q = L(f.server.stt_queued), s0 = L(f.server.stt_start), s1 = L(f.server.stt_end);
    if (q != null && s0 != null && s0 > q) stt.blocks.push({ from: q, to: s0, kind: "light", title: `Warteschlange/Upload ${fmtMs(s0 - q)}` });
    if (s0 != null && s1 != null) stt.blocks.push({ from: s0, to: s1, kind: "solid", title: `Erkennung ${fmtMs(s1 - s0)}` });
    if (f.failure) {
      const at = L(f.failure.t);
      if (at != null) stt.blocks.push({ from: at, to: at + 150, kind: "hatch", title: f.failure.point === "stt_rejected" ? "Erkennung abgelehnt (belegt)" : "Erkennung fehlgeschlagen" });
    }
  });
  if (!synced && turn.source === "voice") {
    const last = frags[frags.length - 1];
    const d = last?.server.stt_end != null && last?.server.stt_start != null ? last.server.stt_end - last.server.stt_start : null;
    stt.note = `Erkennung ${fmtMs(d)} · Uhrabgleich läuft`;
  }
  const dispatch = L(turn.server.turn_dispatch) ?? (turn.source === "text" ? turn.t0 : null);
  const first = L(turn.server.llm_first_token);
  if (dispatch != null && first != null) llm.blocks.push({ from: dispatch, to: first, kind: "light", title: `Bis erstes Token ${fmtMs(first - dispatch)}` });
  else if (dispatch != null && !turn.stop && !turn.server.generation_done)
    llm.blocks.push({ from: dispatch, to: now, kind: "light", title: "Denkt nach …" });
  if (first != null) llm.blocks.push({ from: first, to: genEnd ?? now, kind: "solid", title: "Antwort wird geschrieben" });
  if (!synced) {
    const c = turn.server.llm_first_token != null && turn.server.turn_dispatch != null ? turn.server.llm_first_token - turn.server.turn_dispatch : null;
    llm.note = `Claude ${fmtMs(c)} · Uhrabgleich läuft`;
  }
  for (const [n, s] of Object.entries(turn.sentences)) {
    const r = L(s.request), c = L(s.firstChunk), failed = L(s.failed);
    if (r != null && c != null) tts.blocks.push({ from: r, to: c, kind: "light", title: `Satz ${n}: Vertonung bis erster Ton-Chunk ${fmtMs(c - r)}` });
    if (failed != null) tts.blocks.push({ from: failed, to: failed + 150, kind: "hatch", title: `Satz ${n} ausgefallen` });
  }
  if (turn.audibleStart != null && playEnd != null)
    tts.blocks.push({ from: turn.audibleStart, to: playEnd, kind: "solid", title: `Hörbar ${fmtMs(playEnd - turn.audibleStart)}${turn.mutedAtStop ? " (stumm)" : ""}` });
  if (turn.localStop != null && turn.unspokenUntil != null && turn.unspokenUntil > turn.localStop)
    tts.blocks.push({ from: turn.localStop, to: turn.unspokenUntil, kind: "outline", title: `Nicht mehr gesprochen ${fmtMs(turn.unspokenUntil - turn.localStop)}` });
  if (turn.error) {
    const key = STAGE_TRACK[turn.error.stage ?? ""] ?? "llm";
    const track = { mic, stt, llm, tts }[key];
    track.blocks.push({ from: turn.error.t, to: Math.max(turn.error.t + 300, now), kind: "hatch", title: turn.error.message });
  }
  const tracks = [mic, stt, llm, tts];
  const times = tracks.flatMap((t) => t.blocks.flatMap((b) => [b.from, b.to]));
  const start = Math.min(anchor, ...times);
  const end = Math.max(anchor + 500, ...times);
  return { anchor, aligned, start, end, tracks };
}

/** Turn-Tape: neuester Zug oben, vier Spuren je Zug, ausgerichtet auf t = 0 = Ende deines Satzes. */
export function TurnTape({ telemetry, selectedId, onSelect, details, onToggleDetails, now }: {
  telemetry: Telemetry; selectedId: string | null; onSelect: (id: string) => void; details: boolean; onToggleDetails: () => void; now: number;
}) {
  const rows = telemetry.turns.map((turn) => ({ turn, ...tracksFor(telemetry, turn, now) }));
  if (!rows.length)
    return (
      <section className="cc-tape cc-tape-empty" aria-label="Turn-Tape">
        <p>Noch kein Gesprächszug in dieser Sitzung. Schalte das Mikro ein (M) oder schreib Jarvis (/) – jeder Zug erscheint hier als Messschrieb.</p>
      </section>
    );
  // Gemeinsamer Maßstab, damit Zeilen vergleichbar bleiben (Grenzen gegen Ausreißer).
  const pre = Math.min(8000, Math.max(1000, ...rows.map((r) => r.anchor - r.start)));
  const post = Math.min(30000, Math.max(3000, ...rows.map((r) => r.end - r.anchor)));
  const total = pre + post;
  const pct = (t: number, anchor: number) => Math.max(0, Math.min(100, ((t - anchor + pre) / total) * 100));
  return (
    <section className="cc-tape" aria-label="Turn-Tape">
      <header className="cc-tape-head">
        <h3>Turn-Tape</h3>
        <span className="cc-hint">t = 0 = Ende deines Satzes · ↑/↓ Zug wählen · Enter Rohzeiten · {telemetry.turns.length}/50 Züge</span>
      </header>
      <div className="cc-tape-rows" role="listbox" aria-label="Gesprächszüge" aria-activedescendant={selectedId ? `turn-${selectedId}` : undefined}>
        {rows.map(({ turn, anchor, aligned, tracks }) => {
          const stages = telemetry.stages(turn);
          const selected = turn.id === selectedId;
          const stop = turn.localStop != null ? telemetry.stopAfter(turn) : null;
          const frags = telemetry.fragmentsOf(turn).length;
          return (
            <div key={turn.id} id={`turn-${turn.id}`} role="option" aria-selected={selected} className={`cc-tape-row ${selected ? "selected" : ""}`}
              onClick={() => onSelect(turn.id)}>
              <div className="cc-tape-meta">
                <strong className="mono">{clockOf(turn.createdAt)}</strong>
                <span>{turn.source === "realtime" ? "Echtzeit" : turn.source === "text" ? "getippt" : frags > 1 ? `Sprache · ${frags} Teile` : "Sprache"}</span>
                {turn.supersedes && telemetry.turn(turn.supersedes)?.continuedBy === turn.id && <span className="cc-flag">← Fortsetzung</span>}
                {turn.continuedBy && <span className="cc-flag">fortgesetzt →</span>}
                {!aligned && <span className="cc-flag">{turn.source === "realtime" ? "nur Dauern" : "nicht ausgerichtet"}</span>}
                <span className="cc-tape-total mono" title="Satzende bis erster hörbarer Ton">Gesamt {fmtMs(stages.gesamt)}</span>
              </div>
              <div className="cc-tape-plot">
                {tracks.map((track) => (
                  <div key={track.key} className={`cc-track ${track.key}`}>
                    <span className="cc-track-label">{track.label}</span>
                    <div className="cc-track-lane">
                      {track.blocks.map((b, i) => (
                        <span key={i} className={`cc-blk ${b.kind}`} title={b.title}
                          style={{ left: `${pct(b.from, anchor)}%`, width: `${Math.max(0.4, pct(b.to, anchor) - pct(b.from, anchor))}%` }} />
                      ))}
                      {track.marks.map((m, i) => <span key={`m${i}`} className="cc-glue" title={m.title} style={{ left: `${pct(m.at, anchor)}%` }} />)}
                      {track.note && <span className="cc-track-note">{track.note}</span>}
                    </div>
                  </div>
                ))}
                <div className="cc-overlay">
                {aligned && <span className="cc-t0" style={{ left: `${pct(anchor, anchor)}%` }} aria-hidden="true"><b>t=0</b></span>}
                {turn.localStop != null && (
                  <span className="cc-cut" style={{ left: `${pct(turn.localStop, anchor)}%` }}>
                    <b>{turn.interruptBy === "voice" ? "Unterbrochen" : "Abgebrochen"} · Stopp nach {turn.mutedAtStop ? "– (stumm)" : fmtMs(stop)}</b>
                  </span>
                )}
                {aligned && turn.audibleStart != null && (
                  <span className="cc-bracket" style={{ left: `${pct(anchor, anchor)}%`, width: `${pct(turn.audibleStart, anchor) - pct(anchor, anchor)}%` }}>
                    <b className="mono">{fmtMs(stages.gesamt)}</b>
                  </span>
                )}
                </div>
              </div>
              {selected && details && <TurnDetails telemetry={telemetry} turn={turn} />}
            </div>
          );
        })}
      </div>
      <button type="button" className="cc-btn small cc-tape-toggle" onClick={onToggleDetails} aria-expanded={details}>
        {details ? "Rohzeiten ausblenden" : "Rohzeiten zeigen"} <kbd>Enter</kbd>
      </button>
    </section>
  );
}

function TurnDetails({ telemetry, turn }: { telemetry: Telemetry; turn: Turn }) {
  const anchor = turn.t0;
  const rel = (t: number | null | undefined) => (t == null || anchor == null ? "–" : `${t - anchor >= 0 ? "+" : ""}${fmtMs(t - anchor)}`);
  const L = (v: number | null | undefined) => telemetry.toLocal(v);
  const rows: [string, string, string][] = [];
  telemetry.fragmentsOf(turn).forEach((f, i) => {
    const p = telemetry.fragmentsOf(turn).length > 1 ? ` (Teil ${i + 1})` : "";
    rows.push([`vad_start${p}`, rel(f.vadStart), "Browser"]);
    rows.push([`voiced_end${p} · ${f.cause ?? "–"}`, rel(f.voicedEnd), "Browser"]);
    for (const point of ["audio_end_received", "stt_queued", "stt_sent", "stt_start", "stt_end", "stt_received"] as const)
      rows.push([`${point}${p}`, rel(L(f.server[point])), point === "stt_start" || point === "stt_end" ? "Server (aus Bridge-Dauern)" : "Server"]);
    if (f.failure) rows.push([`${f.failure.point}${p}: ${f.failure.message}`, rel(L(f.failure.t)), "Server"]);
  });
  for (const point of ["turn_dispatch", "llm_first_token", "first_audio_sent", "generation_done", "stop"] as const)
    rows.push([point, rel(L(turn.server[point])), "Server"]);
  for (const [n, s] of Object.entries(turn.sentences)) {
    rows.push([`sentence_ready (${n})`, rel(L(s.ready)), "Server"]);
    rows.push([`tts_request (${n})`, rel(L(s.request)), "Server"]);
    rows.push([`tts_first_chunk (${n})`, rel(L(s.firstChunk)), "Server"]);
    if (s.failed != null) rows.push([`tts_failed (${n})`, rel(L(s.failed)), "Server"]);
  }
  const path = turn.clockPath === "fallback" ? "Browser (currentTime + outputLatency)" : "Browser (getOutputTimestamp)";
  rows.push(["audible_start", rel(turn.audibleStart), path]);
  rows.push(["audible_end", rel(turn.audibleEnd), path]);
  rows.push(["local_stop", rel(turn.localStop), "Browser"]);
  rows.push(["audible_stop", rel(turn.audibleStop), "Browser (Ausgang < −50 dBFS + outputLatency)"]);
  return (
    <div className="cc-raw" onClick={(e) => e.stopPropagation()}>
      <table>
        <caption>Rohzeiten relativ zu t = 0{anchor == null ? " (Zug nicht ausgerichtet)" : ""}</caption>
        <thead><tr><th scope="col">Messpunkt</th><th scope="col">Zeit</th><th scope="col">Uhr</th></tr></thead>
        <tbody>{rows.filter((r) => r[1] !== "–").map((r) => <tr key={r[0]}><td>{r[0]}</td><td className="mono">{r[1]}</td><td>{r[2]}</td></tr>)}</tbody>
      </table>
      <div>
        <h4>Zerlegung von Gesamt</h4>
        <ul>{telemetry.breakdown(turn).map((p) => <li key={p.label}><span>{p.label}</span><span className="mono">{p.label === "Überlappung" ? `−${fmtMs(Math.abs(p.ms ?? 0))}` : fmtMs(p.ms)}</span></li>)}</ul>
        <p className="cc-hint">
          Uhrabgleich: {telemetry.offset == null ? "läuft" : `± ${fmtMs((telemetry.rttMs ?? 0) / 2)} (halbe kleinste RTT)`}
          {turn.stop?.ackMs != null && <> · Server-ACK beim Stopp {fmtMs(turn.stop.ackMs)}</>}
        </p>
      </div>
    </div>
  );
}
