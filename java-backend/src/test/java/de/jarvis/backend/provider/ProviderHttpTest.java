package de.jarvis.backend.provider;

import com.fasterxml.jackson.databind.ObjectMapper;
import de.jarvis.backend.audio.CancellationToken;
import de.jarvis.backend.config.JarvisProperties;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.netty.http.server.HttpServer;
import static org.junit.jupiter.api.Assertions.*;

class ProviderHttpTest {
    @Test void parsesDecodedSseAndIgnoresReasoning() {
        var server = HttpServer.create().host("127.0.0.1").port(0).handle((request, response) ->
                response.header("Content-Type", "text/event-stream").sendString(Flux.just(
                        "event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"delta\":{\"type\":\"text_delta\",\"text\":\"Hallo.\"}}\n\n",
                        "data: {\"type\":\"content_block_delta\",\"delta\":{\"type\":\"thinking_delta\",\"thinking\":\"not-public\"}}\n\n",
                        "data: {\"type\":\"message_stop\"}\n\n").delayElements(Duration.ofMillis(10)))).bindNow();
        try {
            JarvisProperties props = new JarvisProperties();
            props.setAnthropicUrl("http://127.0.0.1:" + server.port() + "/messages");
            var provider = new AnthropicSseProvider(WebClient.builder(), props, new ObjectMapper(), () -> "unit-test-not-a-real-key");
            assertEquals(List.of("Hallo."), provider.generate("Test", new CancellationToken()).collectList().block(Duration.ofSeconds(3)));
            assertFalse(Thread.currentThread().isInterrupted());
        } finally { server.disposeNow(); }
    }

    @Test void aggregatesMp3TransportChunksIntoOneSentenceAndUsesCorrectPath() {
        AtomicReference<String> path = new AtomicReference<>();
        var server = HttpServer.create().host("127.0.0.1").port(0).handle((request, response) -> {
            path.set(request.uri());
            return response.header("Content-Type", "audio/mpeg").sendByteArray(Flux.just(new byte[]{1, 2}, new byte[]{3, 4})
                    .delayElements(Duration.ofMillis(10)));
        }).bindNow();
        try {
            JarvisProperties props = new JarvisProperties();
            props.setElevenlabsUrl("http://127.0.0.1:" + server.port() + "/v1/text-to-speech");
            props.setElevenlabsVoiceId("testvoice");
            var provider = new ElevenLabsTtsProvider(WebClient.builder(), props, () -> "unit-test-not-a-real-key");
            List<byte[]> frames = provider.synthesize("Test", new CancellationToken()).collectList().block(Duration.ofSeconds(3));
            assertEquals(1, frames.size());
            assertArrayEquals(new byte[]{1, 2, 3, 4}, frames.getFirst());
            assertEquals("audio/mpeg", provider.mime());
            assertEquals("/v1/text-to-speech/testvoice/stream?output_format=mp3_44100_128", path.get());
        } finally { server.disposeNow(); }
    }

    @Test void sendsGateContextAndReadsTheDecision() {
        AtomicReference<String> body = new AtomicReference<>();
        var server = HttpServer.create().host("127.0.0.1").port(0).handle((request, response) ->
                request.receive().aggregate().asString(java.nio.charset.StandardCharsets.UTF_8).flatMap(value -> {
                    body.set(value);
                    return response.header("Content-Type", "application/json").sendString(reactor.core.publisher.Mono.just(
                            "{\"text\":\"Schatz?\",\"aktion\":\"ignorieren\",\"grund\":\"nicht an Jarvis\"}")).then();
                })).bindNow();
        try {
            JarvisProperties props = new JarvisProperties();
            props.setDemo(false);
            props.setSttUrl("http://127.0.0.1:" + server.port() + "/transcriptions");
            var provider = new SttProvider(WebClient.builder(), props);
            String lang = "Jarvis sagt gerade: " + "ä".repeat(4000) + " Ende des Satzes.";
            var result = provider.transcribeTimed(new byte[6400], new CancellationToken(),
                    new SttProvider.GateContext(lang, true)).block(Duration.ofSeconds(3));
            assertEquals("ignorieren", result.aktion());
            assertEquals("nicht an Jarvis", result.grund());
            assertTrue(body.get().contains("name=\"pruefen\""));
            assertTrue(body.get().contains("Ende des Satzes."), "das zuletzt Gesagte bleibt beim Kürzen erhalten");
            assertTrue(SttProvider.tail(lang, SttProvider.MAX_CONTEXT_BYTES).getBytes(java.nio.charset.StandardCharsets.UTF_8).length
                    <= SttProvider.MAX_CONTEXT_BYTES);
            assertNull(SttProvider.aktion("loeschen"), "unbekannte Aktion gilt als keine Prüfung");
            assertTrue(SttProvider.isLocal("http://127.0.0.1:8766/internal/transcribe"));
            assertFalse(SttProvider.isLocal("https://api.openai.com/v1/audio/transcriptions"), "Kontext nie an fremde Dienste");
        } finally { server.disposeNow(); }
    }

    @Test void transcribesLocalMultipartWavAndDemoIsHonest() {
        AtomicReference<String> body = new AtomicReference<>();
        var server = HttpServer.create().host("127.0.0.1").port(0).handle((request, response) ->
                request.receive().aggregate().asString().flatMap(value -> {
                    body.set(value);
                    return response.header("Content-Type", "application/json").sendString(reactor.core.publisher.Mono.just("{\"text\":\"Testaufnahme\",\"bridge_wait_ms\":3.5,\"decode_ms\":412.0}")).then();
                })).bindNow();
        try {
            JarvisProperties props = new JarvisProperties();
            props.setDemo(false);
            props.setSttUrl("http://127.0.0.1:" + server.port() + "/transcriptions");
            var provider = new SttProvider(WebClient.builder(), props);
            assertEquals("Testaufnahme", provider.transcribe(new byte[6400], new CancellationToken()).block(Duration.ofSeconds(3)));
            var timed = provider.transcribeTimed(new byte[6400], new CancellationToken()).block(Duration.ofSeconds(3));
            assertEquals(3.5, timed.bridgeWaitMs());
            assertEquals(412.0, timed.decodeMs());
            assertTrue(body.get().contains("capture.wav"));
            assertTrue(body.get().contains("RIFF"));
            props.setDemo(true);
            assertTrue(provider.transcribe(new byte[6400], new CancellationToken()).block().contains("nicht transkribiert"));
        } finally { server.disposeNow(); }
    }
}
