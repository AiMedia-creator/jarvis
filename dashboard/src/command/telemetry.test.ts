import { describe, expect, it } from "vitest";
import { Telemetry } from "./telemetry";
import { tracksFor } from "./TurnTape";

const U1 = "11111111-1111-4111-8111-111111111111";
const U2 = "22222222-2222-4222-8222-222222222222";

/** Server läuft 10 000 ms „vor“ dem Browser; RTT 4 ms. */
function synced(): Telemetry {
  const t = new Telemetry();
  t.onPong(100, 10_000 + 102 + 5, 120); // RTT 20, schlecht
  t.onPong(200, 10_000 + 202, 204); // RTT 4, beste Messung
  return t;
}
const trace = (t: Telemetry, point: string, serverMs: number, extra: Record<string, unknown> = {}) =>
  t.onServer({ type: "TRACE", point, t_ms: serverMs, ...extra });

function voiceTurn(t: Telemetry) {
  t.onMark({ name: "vad_start", t: 1000, utterance: U1 });
  t.onMark({ name: "voiced_end", t: 3000, utterance: U1, cause: "vad", hangoverMs: 1200 });
  trace(t, "audio_end_received", 14_210, { utterance_id: U1 });
  trace(t, "stt_queued", 14_212, { utterance_id: U1 });
  trace(t, "stt_sent", 14_212, { utterance_id: U1 });
  trace(t, "stt_start", 14_215, { utterance_id: U1 });
  trace(t, "stt_end", 14_615, { utterance_id: U1 });
  trace(t, "stt_received", 14_617, { utterance_id: U1 });
  t.onServer({ type: "TURN_START", stream_id: "s1", source: "voice", utterance_ids: [U1], supersedes: null });
  trace(t, "turn_dispatch", 14_620, { stream_id: "s1" });
  trace(t, "llm_first_token", 15_420, { stream_id: "s1" });
  trace(t, "sentence_ready", 15_600, { stream_id: "s1", n: 1 });
  trace(t, "tts_request", 15_601, { stream_id: "s1", n: 1 });
  trace(t, "tts_first_chunk", 15_901, { stream_id: "s1", n: 1 });
  trace(t, "first_audio_sent", 15_902, { stream_id: "s1" });
  t.onMark({ name: "audible_start", t: 5_950, stream: "s1", clock: "output_timestamp" });
}

describe("Telemetrie der Sprachkette", () => {
  it("nimmt für den Uhrabgleich die Messung mit der kleinsten RTT", () => {
    const t = synced();
    expect(t.rttMs).toBe(4);
    expect(t.offset).toBe(10_000);
    expect(t.toLocal(15_000)).toBe(5_000);
  });

  it("berechnet Kachelwerte und Zerlegung aus genau einer Messstelle", () => {
    const t = synced();
    voiceTurn(t);
    const turn = t.turns[0];
    expect(turn.t0).toBe(3000);
    expect(t.stages(turn)).toEqual({ mikro: 2000, erkennung: 400, claude: 800, stimme: 300, gesamt: 2950 });
    const parts = Object.fromEntries(t.breakdown(turn).map((p) => [p.label, p.ms]));
    expect(parts["Nachlauf"]).toBe(1200);
    expect(parts["Warteschlange"]).toBe(3);
    expect(parts["Sammeln"]).toBe(5);
    expect(parts["Auslieferung"]).toBe(48); // 5950 − (15902 − 10000)
    // Rest = Gesamt − Summe der gemessenen Abschnitte; keine Stufe größer als Gesamt
    const known = 1200 + 3 + 400 + 5 + 800 + 180 + 300 + 48;
    expect(parts["Übergaben/Netz"]).toBe(2950 - known);
    for (const value of Object.values(t.stages(turn))) expect(value!).toBeLessThanOrEqual(2950);
  });

  it("führt mehrere Fragmente als eine Zeile, t = 0 ist das Ende des letzten", () => {
    const t = synced();
    t.onMark({ name: "vad_start", t: 100, utterance: U1 });
    t.onMark({ name: "voiced_end", t: 900, utterance: U1, cause: "vad", hangoverMs: 1200 });
    t.onMark({ name: "vad_start", t: 1500, utterance: U2 });
    t.onMark({ name: "voiced_end", t: 2500, utterance: U2, cause: "manual", hangoverMs: 0 });
    t.onServer({ type: "TURN_START", stream_id: "s2", utterance_ids: [U1, U2] });
    expect(t.turns).toHaveLength(1);
    expect(t.turns[0].t0).toBe(2500);
    expect(t.turns[0].t0Cause).toBe("manual");
    expect(t.breakdown(t.turns[0]).some((p) => p.label === "Nachlauf")).toBe(false);
  });

  it("markiert Fortsetzungen in beiden Zeilen", () => {
    const t = synced();
    t.onServer({ type: "TURN_START", stream_id: "a", source: "text" });
    t.onServer({ type: "STREAM_STOPPED", stream_id: "a", reason: "continuation", ack_ms: 0 });
    t.onServer({ type: "TURN_START", stream_id: "b", source: "voice", utterance_ids: [U1], supersedes: "a" });
    expect(t.turn("a")!.continuedBy).toBe("b");
    expect(t.turns[0].supersedes).toBe("a");
  });

  it("misst die gefühlte Stoppzeit ab dem neuen Einsatz bis zur Stille am Ausgang", () => {
    const t = synced();
    voiceTurn(t);
    t.onMark({ name: "vad_start", t: 7000, utterance: U2 });
    t.onMark({ name: "local_stop", t: 7060, stream: "s1", until: 9000 });
    t.onMark({ name: "audible_stop", t: 7110, stream: "s1" });
    const turn = t.turn("s1")!;
    expect(turn.interruptBy).toBe("voice");
    expect(t.stopAfter(turn)).toBe(110);
    expect(turn.unspokenUntil).toBe(9000);
    expect(t.bargeStats()).toEqual({ count: 1, median: 110 });
  });

  it("zeigt im stummen Zustand keine Stoppzeit", () => {
    const t = synced();
    voiceTurn(t);
    t.onMark({ name: "local_stop", t: 7060, stream: "s1", muted: true });
    t.onMark({ name: "audible_stop", t: 7100, stream: "s1" });
    expect(t.stopAfter(t.turn("s1")!)).toBeNull();
  });
});

describe("Zustandsableitung", () => {
  const flags = { connected: true, recording: false, audible: false, now: 0 };

  it("folgt dem Vorrang Offline > Fehler > Hört zu > Spricht > Denkt nach > Erkennt > Bereit", () => {
    const t = synced();
    expect(t.derive({ ...flags, now: 1 }).active).toBe("bereit");
    expect(t.derive({ ...flags, recording: true, audible: true, now: 1 }).active).toBe("hoert");
    expect(t.derive({ ...flags, recording: true, audible: true, now: 1 }).also).toContain("spricht");
    t.onServer({ type: "ERROR", stage: "stt", message: "Spracherkennung ist belegt" }, 50);
    expect(t.derive({ ...flags, recording: true, now: 60 }).active).toBe("fehler");
    expect(t.derive({ ...flags, connected: false, now: 60 }).active).toBe("offline");
    // Haltedauer 8 s, danach wieder normal
    expect(t.derive({ ...flags, now: 8_100 }).active).toBe("bereit");
  });

  it("beendet „Fehler“ beim nächsten Einsatz", () => {
    const t = synced();
    t.onServer({ type: "ERROR", stage: "llm", message: "Claude hat nicht geantwortet." }, 50);
    t.onMark({ name: "vad_start", t: 100, utterance: U1 });
    expect(t.derive({ ...flags, recording: true, now: 120 }).active).toBe("hoert");
  });

  it("zeigt zwischen Satzende und Antwort nie „Bereit“", () => {
    const t = synced();
    t.onMark({ name: "vad_start", t: 1000, utterance: U1 });
    t.onMark({ name: "voiced_end", t: 3000, utterance: U1, cause: "vad", hangoverMs: 1200 });
    // Aufnahme lokal zu Ende, Server hat AUDIO_END noch nicht bestätigt -> Hört zu
    expect(t.derive({ ...flags, now: 4300 }).active).toBe("hoert");
    trace(t, "audio_end_received", 14_300, { utterance_id: U1 });
    trace(t, "stt_queued", 14_301, { utterance_id: U1 });
    expect(t.derive({ ...flags, now: 4400 }).active).toBe("erkennt");
    trace(t, "stt_received", 14_700, { utterance_id: U1 });
    expect(t.derive({ ...flags, now: 4710 }).active).toBe("erkennt"); // Sammeln
    t.onServer({ type: "TURN_START", stream_id: "s1", utterance_ids: [U1] });
    trace(t, "turn_dispatch", 14_710, { stream_id: "s1" });
    expect(t.derive({ ...flags, now: 4720 }).active).toBe("denkt");
    t.onMark({ name: "audible_start", t: 5000, stream: "s1" });
    expect(t.derive({ ...flags, audible: true, now: 5010 }).active).toBe("spricht");
  });

  it("meldet einen komplett ausgefallenen Ton als Fehler statt ewig „Denkt nach“", () => {
    const t = synced();
    t.onServer({ type: "TURN_START", stream_id: "s1", source: "text" });
    trace(t, "sentence_ready", 11_000, { stream_id: "s1", n: 1 });
    trace(t, "tts_failed", 11_200, { stream_id: "s1", n: 1 });
    trace(t, "generation_done", 11_300, { stream_id: "s1" });
    const state = t.derive({ ...flags, now: 1400 });
    expect(state.active).toBe("fehler");
    expect(state.error?.stage).toBe("tts");
  });
});

describe("Turn-Tape", () => {
  it("zeichnet ohne Uhrabgleich keine Server-Blöcke, nur Dauern", () => {
    const t = new Telemetry();
    voiceTurn(t);
    const { tracks } = tracksFor(t, t.turns[0], 6000);
    const stt = tracks.find((x) => x.key === "stt")!;
    expect(stt.blocks).toHaveLength(0);
    expect(stt.note).toContain("Erkennung 400 ms");
    expect(stt.note).toContain("Uhrabgleich läuft");
    // Browser-Marken stehen trotzdem als Position
    expect(tracks.find((x) => x.key === "mic")!.blocks.length).toBeGreaterThan(0);
  });

  it("zeigt Barge-in als Schnitt mit ungesprochenem Rest", () => {
    const t = synced();
    voiceTurn(t);
    t.onMark({ name: "vad_start", t: 7000, utterance: U2 });
    t.onMark({ name: "local_stop", t: 7060, stream: "s1", until: 9000 });
    const tts = tracksFor(t, t.turn("s1")!, 7100).tracks.find((x) => x.key === "tts")!;
    expect(tts.blocks.some((b) => b.kind === "outline" && b.from === 7060 && b.to === 9000)).toBe(true);
    expect(tts.blocks.some((b) => b.kind === "solid" && b.to === 7060)).toBe(true);
  });
});
