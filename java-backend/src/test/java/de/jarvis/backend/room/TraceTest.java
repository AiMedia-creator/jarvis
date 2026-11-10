package de.jarvis.backend.room;

import static org.junit.jupiter.api.Assertions.*;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import de.jarvis.backend.audio.CancellationToken;
import de.jarvis.backend.config.JarvisProperties;
import de.jarvis.backend.provider.GenerationProvider;
import de.jarvis.backend.provider.SttProvider;
import de.jarvis.backend.provider.TtsProvider;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CopyOnWriteArrayList;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

/** Die Regie-Kette bekommt echte Messpunkte auf einer monotonen Serveruhr – und Fehler nennen ihre Stufe. */
class TraceTest {
  static final byte[] PCM = new byte[6400];
  final ObjectMapper mapper = new ObjectMapper();
  final List<Map<String, Object>> messages = new CopyOnWriteArrayList<>();
  Room.Client owner;

  static final class TimedStt extends SttProvider {
    final Mono<SttResult> answer;
    TimedStt(Mono<SttResult> answer) { super(WebClient.builder(), new JarvisProperties()); this.answer = answer; }
    @Override public Mono<SttResult> transcribeTimed(byte[] pcm, CancellationToken token) { return answer; }
  }

  Room room(GenerationProvider g, TtsProvider tts, SttProvider stt) {
    Room r = new Room("room1234", 4, 16, g, tts, stt, mapper);
    owner = r.join("owner1234", "main");
    r.controlFlux(owner).subscribe(text -> {
      try { messages.add(mapper.readValue(text, new TypeReference<Map<String, Object>>() { })); }
      catch (Exception ignored) { }
    });
    return r;
  }

  List<String> points() {
    return messages.stream().filter(m -> "TRACE".equals(m.get("type"))).map(m -> (String) m.get("point")).toList();
  }

  void waitUntil(java.util.function.BooleanSupplier cond) {
    long end = System.currentTimeMillis() + 3000;
    while (!cond.getAsBoolean() && System.currentTimeMillis() < end) {
      try { Thread.sleep(10); } catch (InterruptedException e) { return; }
    }
  }

  @Test void sprachzugLiefertMesspunkteInReihenfolge() {
    SttProvider stt = new TimedStt(Mono.just(new SttProvider.SttResult("Wie spät ist es?", 2.0, 30.0))
        .delayElement(Duration.ofMillis(60)));
    GenerationProvider g = (p, t) -> Flux.just("Es ist zwölf. ", "Schönen Tag.");
    TtsProvider tts = (s, c) -> Flux.just(new byte[] {1, 2, 3});
    Room r = room(g, tts, stt);
    UUID u = UUID.randomUUID();
    r.audioStart("owner1234", u, 16_000);
    r.transcribe("owner1234", u, PCM);
    waitUntil(() -> points().contains("generation_done"));
    List<String> p = points();
    for (String expected : List.of("audio_end_received", "stt_queued", "stt_sent", "stt_start", "stt_end", "stt_received",
        "turn_dispatch", "llm_first_token", "sentence_ready", "tts_request", "tts_first_chunk", "first_audio_sent", "generation_done"))
      assertTrue(p.contains(expected), "fehlt: " + expected + " in " + p);
    assertTrue(p.indexOf("stt_sent") < p.indexOf("turn_dispatch"));
    assertTrue(p.indexOf("llm_first_token") < p.indexOf("first_audio_sent"));
    // Abgeleitete STT-Zeiten liegen zwischen Senden und Empfang, Dekodierdauer bleibt erhalten.
    double sent = tMs("stt_sent"), start = tMs("stt_start"), end = tMs("stt_end"), received = tMs("stt_received");
    assertTrue(sent <= start && start <= end && end <= received, sent + " " + start + " " + end + " " + received);
    assertEquals(30.0, end - start, 0.2);
    Map<String, Object> turn = messages.stream().filter(m -> "TURN_START".equals(m.get("type"))).findFirst().orElseThrow();
    assertEquals(List.of(u.toString()), turn.get("utterance_ids"));
    assertEquals("voice", turn.get("source"));
    Map<String, Object> listening = messages.stream()
        .filter(m -> "STATE".equals(m.get("type")) && "listening".equals(m.get("status"))).findFirst().orElseThrow();
    assertEquals(u.toString(), listening.get("utterance_id"));
    assertNotNull(listening.get("t_ms"));
  }

  double tMs(String point) {
    return messages.stream().filter(m -> "TRACE".equals(m.get("type")) && point.equals(m.get("point")))
        .map(m -> ((Number) m.get("t_ms")).doubleValue()).findFirst().orElseThrow();
  }

  @Test void fehlgeschlageneErkennungMeldetStufeStattStummZuBleiben() {
    SttProvider stt = new TimedStt(Mono.error(new SttProvider.SttRejected("Spracherkennung ist belegt: Es wird eine Aufnahme nach der anderen erkannt.")));
    Room r = room((p, t) -> Flux.never(), (s, c) -> Flux.empty(), stt);
    UUID u = UUID.randomUUID();
    r.audioStart("owner1234", u, 16_000);
    r.transcribe("owner1234", u, PCM);
    waitUntil(() -> messages.stream().anyMatch(m -> "ERROR".equals(m.get("type"))));
    Map<String, Object> error = messages.stream().filter(m -> "ERROR".equals(m.get("type"))).findFirst().orElseThrow();
    assertEquals("stt", error.get("stage"));
    assertTrue(((String) error.get("message")).contains("belegt"));
    assertTrue(points().contains("stt_rejected"));
    assertEquals("idle", r.status());
  }

  @Test void pongTraegtServeruhr() {
    Room r = room((p, t) -> Flux.never(), (s, c) -> Flux.empty(), null);
    r.ping(owner, 1234.5);
    waitUntil(() -> messages.stream().anyMatch(m -> "PONG".equals(m.get("type"))));
    Map<String, Object> pong = messages.stream().filter(m -> "PONG".equals(m.get("type"))).findFirst().orElseThrow();
    assertEquals(1234.5, ((Number) pong.get("sent_at")).doubleValue());
    assertTrue(((Number) pong.get("server_t_ms")).doubleValue() >= 0);
  }

  @Test void abbruchWirdAlsStoppPunktGemeldet() {
    Room r = room((p, t) -> Flux.never(), (s, c) -> Flux.empty(), null);
    r.start("owner1234", "Frage");
    assertTrue(r.stop("owner1234", null, "manual"));
    assertTrue(points().contains("stop"));
  }
}
