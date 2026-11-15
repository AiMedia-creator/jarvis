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
    /** Entscheidung des Sprach-Türstehers je Aufnahme-Länge (null = keine Prüfung) und die mitgeschickten Kontexte. */
    final Map<Integer, String> aktionen = new ConcurrentHashMap<>();
    final List<GateContext> gates = new CopyOnWriteArrayList<>();
    FakeStt() { super(WebClient.builder(), new JarvisProperties()); }
    @Override public Mono<SttResult> transcribeTimed(byte[] pcm, CancellationToken token, GateContext gate) {
      int key = pcm.length;
      gates.add(gate);
      return Mono.just(new SttResult(texts.get(key), 1.0, 2.0, aktionen.get(key), aktionen.containsKey(key) ? "test" : null)).delayElement(Duration.ofMillis(delays.getOrDefault(key, 5L)))
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

  @Test void verworfeneZusatzaufnahmeBeantwortetDieFrageTrotzdem() {
    // Ein Geräusch während Jarvis denkt startet eine Aufnahme, die der Browser gleich wieder verwirft (AUDIO_ABORT).
    // Die bereits gestellte Frage darf dabei nicht verloren gehen und der Raum nicht auf „hört zu“ stehen bleiben.
    Room r = room(thinkingForever(), (s, c) -> Flux.empty(), new FakeStt());
    r.start("owner1234", "Was steht heute an?");
    UUID u = UUID.randomUUID();
    r.audioStart("owner1234", u, 16_000);
    r.audioAbort("owner1234", u);
    waitUntil(() -> prompts.size() == 2);
    assertEquals(List.of("Was steht heute an?", "Was steht heute an?"), prompts);
  }

  @Test void verworfeneAufnahmeOhneOffeneFrageFuehrtZuRuhe() {
    Room r = room(thinkingForever(), (s, c) -> Flux.empty(), new FakeStt());
    UUID u = UUID.randomUUID();
    r.audioStart("owner1234", u, 16_000);
    r.audioAbort("owner1234", u);
    assertEquals(0, prompts.size());
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

  Room speakingRoom(FakeStt stt) {
    GenerationProvider g = (p, t) -> { prompts.add(p); return Flux.just("Hier ist die Antwort. ").concatWith(Flux.never()); };
    Room r = room(g, (s, c) -> Flux.just(new byte[] {1, 2}), stt);
    r.start("owner1234", "Alte Frage");
    waitUntil(() -> "speaking".equals(r.status()));
    return r;
  }

  void pause() { try { Thread.sleep(300); } catch (InterruptedException ignored) { } }

  @Test void vorlaeufigesReinredenOhneAnspracheLaesstJarvisWeiterreden() {
    FakeStt stt = new FakeStt();
    stt.texts.put(6400, "Schatz, wo ist der Schlüssel?");
    stt.aktionen.put(6400, "ignorieren");
    Room r = speakingRoom(stt);
    UUID u = UUID.randomUUID();
    r.audioStart("owner1234", u, 16_000, true);   // Browser hat Jarvis nur leiser gestellt
    assertEquals(0, r.interrupts(), "vorläufig: noch nicht unterbrochen");
    r.transcribe("owner1234", u, PCM);
    pause();
    assertEquals(List.of("Alte Frage"), prompts);
    assertEquals(0, r.interrupts(), "Jarvis spricht weiter");
    assertTrue(stt.gates.get(0).jarvisSpricht());
    assertTrue(stt.gates.get(0).kontext().contains("Hier ist die Antwort"), "Kontext = was Jarvis gerade sagt");
  }

  @Test void vorlaeufigesReinredenMitAnspracheUnterbrichtUndBeantwortet() {
    FakeStt stt = new FakeStt();
    stt.texts.put(6400, "Nein, nimm lieber Donnerstag");
    stt.aktionen.put(6400, "antworten");
    Room r = speakingRoom(stt);
    UUID u = UUID.randomUUID();
    r.audioStart("owner1234", u, 16_000, true);
    r.transcribe("owner1234", u, PCM);
    waitUntil(() -> prompts.size() == 2);
    assertEquals("Nein, nimm lieber Donnerstag", prompts.get(1));
    assertEquals(1, r.interrupts());
  }

  @Test void stoppWunschHaeltNurAn() {
    FakeStt stt = new FakeStt();
    stt.texts.put(6400, "Danke, das reicht mir");
    stt.aktionen.put(6400, "stoppen");
    Room r = speakingRoom(stt);
    UUID u = UUID.randomUUID();
    r.audioStart("owner1234", u, 16_000, true);
    r.transcribe("owner1234", u, PCM);
    waitUntil(() -> r.interrupts() == 1);
    pause();
    assertEquals(List.of("Alte Frage"), prompts, "keine neue Antwort auf „Danke, das reicht“");
    assertEquals("idle", r.status());
  }

  @Test void ignorierteAeusserungOhneLaufendeAntwortStelltNichtsAn() {
    FakeStt stt = new FakeStt();
    stt.texts.put(6400, "Boss, Beginn, Boss.");
    stt.aktionen.put(6400, "ignorieren");
    Room r = room(thinkingForever(), (s, c) -> Flux.empty(), stt);
    UUID u = UUID.randomUUID();
    r.audioStart("owner1234", u, 16_000);
    r.transcribe("owner1234", u, PCM);
    pause();
    assertTrue(prompts.isEmpty());
    assertEquals("idle", r.status());
    assertFalse(stt.gates.get(0).jarvisSpricht());
  }

  @Test void stoppWaehrendJarvisDenktStelltDieFrageNichtNeu() {
    FakeStt stt = new FakeStt();
    stt.texts.put(6400, "Stopp");
    stt.aktionen.put(6400, "stoppen");
    Room r = room(thinkingForever(), (s, c) -> Flux.empty(), stt);
    r.start("owner1234", "Erklär mir alles über Steuern");
    UUID u = UUID.randomUUID();
    r.audioStart("owner1234", u, 16_000);          // nicht vorläufig: er denkt noch, Antwort wird angehalten
    r.transcribe("owner1234", u, PCM);
    pause();
    assertEquals(List.of("Erklär mir alles über Steuern"), prompts, "angehaltene Frage wird nicht erneut gestellt");
    assertTrue(stt.gates.get(0).jarvisSpricht(), "Jarvis war dran");
  }

  @Test void fortsetzungEinerAngehaltenenFrageWirdNieAlleinVerworfen() {
    FakeStt stt = new FakeStt();
    stt.texts.put(6400, "und morgen?");
    stt.aktionen.put(6400, "ignorieren");          // allein klingt der Satzrest unklar
    Room r = room(thinkingForever(), (s, c) -> Flux.empty(), stt);
    r.start("owner1234", "Wie wird das Wetter heute");
    UUID u = UUID.randomUUID();
    r.audioStart("owner1234", u, 16_000);          // er denkt noch: Frage wird angehalten und ergänzt
    r.transcribe("owner1234", u, PCM);
    waitUntil(() -> prompts.size() == 2);
    assertEquals("Wie wird das Wetter heute und morgen?", prompts.get(1));
  }

  @Test void stoppLaesstEineSchonLaufendeAufnahmeLeben() {
    FakeStt stt = new FakeStt();
    byte[] stopp = new byte[6400], weiter = new byte[12800];
    stt.texts.put(stopp.length, "Stopp");
    stt.aktionen.put(stopp.length, "stoppen");
    stt.delays.put(stopp.length, 100L);
    stt.texts.put(weiter.length, "Was steht morgen an?");
    stt.aktionen.put(weiter.length, "antworten");
    Room r = speakingRoom(stt);
    UUID a = UUID.randomUUID(), b = UUID.randomUUID();
    r.audioStart("owner1234", a, 16_000, true);
    r.transcribe("owner1234", a, stopp);
    r.audioStart("owner1234", b, 16_000, true);   // redet schon weiter, während „Stopp“ noch erkannt wird
    waitUntil(() -> r.interrupts() == 1);
    r.transcribe("owner1234", b, weiter);
    waitUntil(() -> prompts.size() == 2);
    assertEquals("Was steht morgen an?", prompts.get(1));
    assertTrue(stt.gates.get(1).jarvisSpricht(), "zweite Aufnahme bleibt als Reinreden markiert");
  }

  @Test void zuruecksetzenVergisstDenKontext() {
    FakeStt stt = new FakeStt();
    stt.texts.put(6400, "Hallo");
    Room r = speakingRoom(stt);
    r.reset("owner1234");
    UUID u = UUID.randomUUID();
    r.audioStart("owner1234", u, 16_000);
    r.transcribe("owner1234", u, PCM);
    waitUntil(() -> !stt.gates.isEmpty());
    assertEquals("", stt.gates.get(0).kontext());
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
