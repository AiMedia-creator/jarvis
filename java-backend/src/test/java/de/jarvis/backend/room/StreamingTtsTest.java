package de.jarvis.backend.room;

import static org.junit.jupiter.api.Assertions.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import de.jarvis.backend.audio.AudioFrame;
import de.jarvis.backend.audio.CancellationToken;
import de.jarvis.backend.config.JarvisProperties;
import de.jarvis.backend.provider.GenerationProvider;
import de.jarvis.backend.provider.PythonBridgeTtsProvider;
import de.jarvis.backend.provider.TtsChunk;
import de.jarvis.backend.provider.TtsProvider;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicBoolean;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.netty.http.server.HttpServer;

/** Etappe 5: Satz-Vertonung als Stücke – Reihenfolge, Format, Ausrichtung und Abbruch bis zur Brücke. */
class StreamingTtsTest {
  private static final String PCM = "audio/pcm;rate=22050";

  @Test void chunksOfParallelSentencesStayInSentenceOrder() {
    GenerationProvider g = (p, t) -> Flux.just("Erster Satz. ", "Zweiter Satz.");
    TtsProvider tts = new TtsProvider() {
      public Flux<byte[]> synthesize(String s, CancellationToken c) { return Flux.empty(); }
      public Flux<TtsChunk> stream(String s, CancellationToken c) {
        String tag = s.startsWith("Erster") ? "A" : "B";
        long delay = tag.equals("A") ? 120 : 5; // der zweite Satz ist schneller fertig
        return Flux.range(0, 3).delayElements(Duration.ofMillis(delay))
            .map(i -> new TtsChunk(PCM, (tag + i).getBytes(StandardCharsets.UTF_8)));
      }
    };
    Room r = new Room("room1234", 4, 16, g, tts, new ObjectMapper());
    r.join("owner1234", "main");
    var frames = new CopyOnWriteArrayList<AudioFrame>();
    var sub = r.audioFlux().subscribe(frames::add);
    r.start("owner1234", "los");
    long end = System.currentTimeMillis() + 3000;
    while (!"idle".equals(r.status()) && System.currentTimeMillis() < end) {
      try { Thread.sleep(20); } catch (InterruptedException e) { break; }
    }
    sub.dispose();
    assertEquals(List.of("A0", "A1", "A2", "B0", "B1", "B2"),
        frames.stream().map(f -> new String(f.payloadCopy(), StandardCharsets.UTF_8)).toList());
    assertTrue(frames.stream().allMatch(f -> PCM.equals(f.mime())));
    for (int i = 1; i < frames.size(); i++) assertEquals(frames.get(i - 1).sequence() + 1, frames.get(i).sequence());
  }

  @Test void unknownMimeIsNotPassedThrough() {
    assertTrue(TtsChunk.allowedMime(PCM));
    assertTrue(TtsChunk.allowedMime("audio/mpeg"));
    assertFalse(TtsChunk.allowedMime("audio/pcm;rate=12345"));
    assertFalse(TtsChunk.allowedMime("text/html"));
  }

  @Test void bridgeStreamIsAlignedPcmAndCancelClosesConnection() throws Exception {
    AtomicBoolean cancelled = new AtomicBoolean();
    var server = HttpServer.create().host("127.0.0.1").port(0).route(routes -> routes.post("/internal/tts/stream",
        (request, response) -> response.header("X-Audio-Mime", PCM)
            .sendByteArray(Flux.just(new byte[] {1, 2, 3}, new byte[] {4, 5}, new byte[] {6, 7, 8})
                .concatWith(Flux.<byte[]>never().doOnCancel(() -> cancelled.set(true))))
            .then())).bindNow();
    try {
      JarvisProperties props = new JarvisProperties();
      props.setBridgeUrl("http://127.0.0.1:" + server.port());
      props.setBridgeToken("unit-test-bridge-key");
      var provider = new PythonBridgeTtsProvider(WebClient.builder(), props);
      var token = new CancellationToken();
      List<TtsChunk> got = provider.stream("Hallo.", token).take(2).collectList().block(Duration.ofSeconds(5));
      assertNotNull(got);
      assertTrue(got.stream().allMatch(c -> PCM.equals(c.mime()) && c.bytes().length % 2 == 0), "ganze 16-bit-Samples");
      byte[] joined = new byte[got.stream().mapToInt(c -> c.bytes().length).sum()];
      int at = 0;
      for (TtsChunk c : got) { System.arraycopy(c.bytes(), 0, joined, at, c.bytes().length); at += c.bytes().length; }
      assertArrayEquals(new byte[] {1, 2, 3, 4}, java.util.Arrays.copyOf(joined, 4));
      long end = System.currentTimeMillis() + 2000;
      while (!cancelled.get() && System.currentTimeMillis() < end) Thread.sleep(20);
      assertTrue(cancelled.get(), "Abbruch muss die Verbindung zur Brücke schließen");
    } finally { server.disposeNow(); }
  }
}
