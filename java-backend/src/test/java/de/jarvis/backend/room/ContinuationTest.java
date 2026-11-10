package de.jarvis.backend.room;

import static org.junit.jupiter.api.Assertions.*;
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
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.CopyOnWriteArrayList;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

/** Weiterreden ergänzt die Frage (statt sie abzubrechen); Abschnitte bleiben in Reihenfolge; Barge-in bleibt Barge-in. */
class ContinuationTest {
  static final byte[] PCM = new byte[6400];

  /** STT-Attrappe: Text und Verzögerung je Aufnahme-Länge-Schlüssel über eine Warteschlange. */
  static final class FakeStt extends SttProvider {
    final Map<Integer, String> texts = new ConcurrentHashMap<>();
    final Map<Integer, Long> delays = new ConcurrentHashMap<>();
    final List<Boolean> cancelled = new CopyOnWriteArrayList<>();
    FakeStt() { super(WebClient.builder(), new JarvisProperties()); }
    @Override public Mono<SttResult> transcribeTimed(byte[] pcm, CancellationToken token) {
      int key = pcm.length;
      return Mono.just(new SttResult(texts.get(key), 1.0, 2.0)).delayElement(Duration.ofMillis(delays.getOrDefault(key, 5L)))
          .doOnCancel(() -> cancelled.add(true));
    }
  }

  final List<String> prompts = new CopyOnWriteArrayList<>();

  Room room(GenerationProvider g, TtsProvider tts, FakeStt stt) {
    Room r = new Room("room1234", 4, 16, g, tts, stt, new ObjectMapper());
    r.join("owner1234", "main");
    return r;
  }

  void waitUntil(java.util.function.BooleanSupplier cond) {
    long end = System.currentTimeMillis() + 3000;
    while (!cond.getAsBoolean() && System.currentTimeMillis() < end) {
      try { Thread.sleep(10); } catch (InterruptedException e) { return; }
    }
  }

  GenerationProvider thinkingForever() {
    return (p, t) -> { prompts.add(p); return Flux.never(); };
  }

  @Test void weiterredenWaehrendJarvisNochDenktErgaenztDieFrage() {
    FakeStt stt = new FakeStt();
    stt.texts.put(6400, "und wie verkaufe ich das an Steuerberater?");
    Room r = room(thinkingForever(), (s, c) -> Flux.empty(), stt);
    r.start("owner1234", "Erklär mir, wie ich ein KI-Produkt baue");
    UUID u = UUID.randomUUID();
    r.audioStart("owner1234", u, 16_000);          // Nutzer redet weiter, Jarvis hat noch nichts gesagt
    r.transcribe("owner1234", u, PCM);
    waitUntil(() -> prompts.size() == 2);
    assertEquals("Erklär mir, wie ich ein KI-Produkt baue und wie verkaufe ich das an Steuerberater?", prompts.get(1));
  }

  @Test void stopVomBrowserBeiSprachbeginnUebernimmtEbenfalls() {
    FakeStt stt = new FakeStt();
    stt.texts.put(6400, "zweiter Teil");
    Room r = room(thinkingForever(), (s, c) -> Flux.empty(), stt);
    r.start("owner1234", "erster Teil");
    assertTrue(r.stop("owner1234", null, "vad"));  // Browser sendet bei Sprachbeginn STOP_STREAM(vad)
    UUID u = UUID.randomUUID();
    r.audioStart("owner1234", u, 16_000);
    r.transcribe("owner1234", u, PCM);
    waitUntil(() -> prompts.size() == 2);
    assertEquals("erster Teil zweiter Teil", prompts.get(1));
  }

  @Test void unterbrechungWaehrendJarvisSprichtIstEchterBargeIn() {
    FakeStt stt = new FakeStt();
    stt.texts.put(6400, "Stopp, andere Frage");
    GenerationProvider g = (p, t) -> { prompts.add(p); return Flux.just("Hier ist die Antwort. ").concatWith(Flux.never()); };
    TtsProvider tts = (s, c) -> Flux.just(new byte[] {1, 2});
    Room r = room(g, tts, stt);
    r.start("owner1234", "Alte Frage");
    waitUntil(() -> "speaking".equals(r.status()));
    UUID u = UUID.randomUUID();
    r.audioStart("owner1234", u, 16_000);
    r.transcribe("owner1234", u, PCM);
    waitUntil(() -> prompts.size() == 2);
    assertEquals("Stopp, andere Frage", prompts.get(1));
  }

  @Test void abschnitteBleibenInReihenfolgeAuchWennDieErkennungUmgekehrtFertigWird() {
    FakeStt stt = new FakeStt();
    byte[] first = new byte[12800], second = new byte[6400];
    stt.texts.put(first.length, "Teil A");
    stt.delays.put(first.length, 300L);            // erster Abschnitt braucht länger
    stt.texts.put(second.length, "Teil B");
    Room r = room((p, t) -> { prompts.add(p); return Flux.never(); }, (s, c) -> Flux.empty(), stt);
    UUID a = UUID.randomUUID(), b = UUID.randomUUID();
    r.audioStart("owner1234", a, 16_000);
    r.transcribe("owner1234", a, first);
    r.audioStart("owner1234", b, 16_000);          // weiterreden: erste Erkennung darf NICHT verworfen werden
    r.transcribe("owner1234", b, second);
    waitUntil(() -> !prompts.isEmpty());
    try { Thread.sleep(400); } catch (InterruptedException ignored) { }
    assertEquals(List.of("Teil A Teil B"), prompts);
    assertTrue(stt.cancelled.isEmpty(), "keine Erkennung abgebrochen");
  }

  @Test void getippteEingabeVerwirftSprachreste() {
    FakeStt stt = new FakeStt();
    stt.texts.put(6400, "alter Sprachrest");
    stt.delays.put(6400, 300L);
    Room r = room(thinkingForever(), (s, c) -> Flux.empty(), stt);
    UUID u = UUID.randomUUID();
    r.audioStart("owner1234", u, 16_000);
    r.transcribe("owner1234", u, PCM);
    r.start("owner1234", "Getippte Frage");
    try { Thread.sleep(500); } catch (InterruptedException ignored) { }
    assertEquals(List.of("Getippte Frage"), prompts);
  }
}
