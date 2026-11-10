package de.jarvis.backend.provider;

import com.fasterxml.jackson.databind.ObjectMapper;
import de.jarvis.backend.audio.CancellationToken;
import de.jarvis.backend.config.JarvisProperties;
import java.time.Duration;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpMethod;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.netty.http.server.HttpServer;
import static org.junit.jupiter.api.Assertions.*;

class PythonBridgeHttpTest {
    private static final String KEY = "unit-test-bridge-key";

    @Test
    void authenticatedNdjsonForwardsRoomAndRequestAndPublishesOnlyPublicEvents() {
        AtomicReference<String> auth = new AtomicReference<>();
        AtomicReference<String> body = new AtomicReference<>();
        var server = HttpServer.create().host("127.0.0.1").port(0).handle((request, response) -> {
            auth.set(request.requestHeaders().get("Authorization"));
            return request.receive().aggregate().asString().flatMap(value -> {
                body.set(value);
                return response.header("Content-Type", "application/x-ndjson").sendString(Flux.just(
                        ": heartbeat\n",
                        "{\"type\":\"log\",\"message\":\"connected\"}\n",
                        "{\"type\":\"token\",\"text\":\"Hallo\"}\n",
                        "{\"type\":\"memory\",\"id\":\"m1\",\"content\":\"recall\"}\n",
                        "{\"type\":\"done\"}\n")).then();
            });
        }).bindNow();
        try {
            JarvisProperties props = props(server, "/bridge");
            UUID id = UUID.randomUUID();
            List<Map<String, Object>> events = new ArrayList<>();
            var provider = new PythonBridgeProvider(WebClient.builder(), props, new ObjectMapper(), () -> KEY);
            var context = new GenerationContext("room a", id, events::add);
            assertEquals(List.of("Hallo"), provider.generate("frage", new CancellationToken(), context)
                    .collectList().block(Duration.ofSeconds(3)));
            assertEquals("Bearer " + KEY, auth.get());
            assertTrue(body.get().contains("\"conversation_id\":\"room a\""));
            assertTrue(body.get().contains(id.toString()));
            assertEquals("LOG", events.getFirst().get("type"));
            assertEquals("MEMORY", events.get(1).get("type"));
        } finally { server.disposeNow(); }
    }

    @Test
    void toolEventsBecomeValidatedPublicToolHintsAndNewsMarketQueriesAreEncoded() {
        List<String> calls = new ArrayList<>();
        var server = HttpServer.create().host("127.0.0.1").port(0).handle((request, response) -> {
            calls.add(request.uri());
            if (request.uri().contains("/internal/generate"))
                return response.header("Content-Type", "application/x-ndjson").sendString(Flux.just(
                        "{\"type\":\"tool\",\"name\":\"nachrichten\"}\n",
                        "{\"type\":\"tool\",\"name\":\"Bad Name; rm -rf\"}\n",
                        "{\"type\":\"token\",\"text\":\"Ok\"}\n",
                        "{\"type\":\"done\"}\n")).then();
            return response.header("Content-Type", "application/json").sendString(Flux.just("{\"status\":\"ok\"}")).then();
        }).bindNow();
        try {
            List<Map<String, Object>> events = new ArrayList<>();
            var provider = new PythonBridgeProvider(WebClient.builder(), props(server, "/bridge"), new ObjectMapper(), () -> KEY);
            provider.generate("x", new CancellationToken(), new GenerationContext("r", UUID.randomUUID(), events::add))
                    .collectList().block(Duration.ofSeconds(3));
            assertEquals(1, events.stream().filter(e -> "TOOL".equals(e.get("type"))).count());
            assertEquals("nachrichten", events.getFirst().get("name"));
            provider.news("wirtschaft", "Kiew & Co").block(Duration.ofSeconds(3));
            provider.market("Apple").block(Duration.ofSeconds(3));
            assertTrue(calls.stream().anyMatch(c -> c.contains("/internal/news?rubrik=wirtschaft&stichwort=Kiew+%26+Co")));
            assertTrue(calls.stream().anyMatch(c -> c.contains("/internal/market?q=Apple")));
        } finally { server.disposeNow(); }
    }

    @Test
    void heartbeatsKeepALongTurnAliveButASilentBridgeTimesOut() {
        var alive = HttpServer.create().host("127.0.0.1").port(0).handle((request, response) ->
                response.header("Content-Type", "application/x-ndjson").sendString(
                        Flux.interval(Duration.ofMillis(100)).take(12).map(i -> "{\"type\":\"heartbeat\"}\n")
                                .concatWith(Flux.just("{\"type\":\"token\",\"text\":\"Spaet\"}\n", "{\"type\":\"done\"}\n"))).then()
        ).bindNow();
        var silent = HttpServer.create().host("127.0.0.1").port(0).handle((request, response) ->
                response.header("Content-Type", "application/x-ndjson").sendString(Flux.<String>never()).then()
        ).bindNow();
        try {
            // 1,2 s ohne Token, aber mit Heartbeats: darf bei 300 ms Inaktivitäts-Frist NICHT abbrechen.
            var provider = new PythonBridgeProvider(WebClient.builder(), props(alive, "/bridge"), new ObjectMapper(), () -> KEY);
            provider.idleTimeout = Duration.ofMillis(300);
            assertEquals(List.of("Spaet"), provider.generate("x", new CancellationToken(),
                    new GenerationContext("r", UUID.randomUUID(), e -> { })).collectList().block(Duration.ofSeconds(5)));
            // Stumme Brücke: bricht nach der Frist ab statt ewig zu hängen.
            var mute = new PythonBridgeProvider(WebClient.builder(), props(silent, "/bridge"), new ObjectMapper(), () -> KEY);
            mute.idleTimeout = Duration.ofMillis(300);
            assertThrows(RuntimeException.class, () -> mute.generate("x", new CancellationToken(),
                    new GenerationContext("r", UUID.randomUUID(), e -> { })).collectList().block(Duration.ofSeconds(5)));
        } finally { alive.disposeNow(); silent.disposeNow(); }
    }

    @Test
    void memoryCrudUsesEncodedQueryAndPathAndNeverSendsTokenInJson() {
        List<String> calls = new ArrayList<>();
        AtomicReference<String> auth = new AtomicReference<>();
        AtomicReference<String> lastBody = new AtomicReference<>("");
        var server = HttpServer.create().host("127.0.0.1").port(0).handle((request, response) -> {
            calls.add(request.method().name() + " " + request.uri());
            auth.set(request.requestHeaders().get("Authorization"));
            return request.receive().aggregate().asString().defaultIfEmpty("").flatMap(value -> {
                lastBody.set(value);
                return response.header("Content-Type", "application/json")
                        .sendString(Flux.just("{\"ok\":true}")).then();
            });
        }).bindNow();
        try {
            var provider = new PythonBridgeProvider(WebClient.builder(), props(server, "/bridge"), new ObjectMapper(), () -> KEY);
            provider.snapshot("a+b & c", 12).block(Duration.ofSeconds(3));
            provider.remember("note", "fact").block(Duration.ofSeconds(3));
            provider.forget("id with/slash").block(Duration.ofSeconds(3));
            assertTrue(calls.get(0).contains("/internal/memory?q=a%2Bb+%26+c&limit=12"));
            assertTrue(calls.get(2).contains("/internal/memory/id%20with%2Fslash"));
            assertEquals("Bearer " + KEY, auth.get());
            assertFalse(lastBody.get().contains(KEY));
        } finally { server.disposeNow(); }
    }

    @Test
    void cancellationPostsOnlyTheSubscribedRequestId() throws Exception {
        CountDownLatch cancelled = new CountDownLatch(1);
        AtomicReference<String> cancelBody = new AtomicReference<>();
        AtomicReference<String> cancelAuth = new AtomicReference<>();
        var server = HttpServer.create().host("127.0.0.1").port(0).handle((request, response) -> {
            if (request.uri().startsWith("/internal/cancel")) {
                cancelAuth.set(request.requestHeaders().get("Authorization"));
                return request.receive().aggregate().asString().flatMap(value -> {
                    cancelBody.set(value);
                    cancelled.countDown();
                    return response.send();
                });
            }
            return response.header("Content-Type", "application/x-ndjson")
                    .sendString(Flux.concat(Flux.just("{\"type\":\"heartbeat\"}\n"), Flux.never()));
        }).bindNow();
        try {
            JarvisProperties props = props(server, "/bridge");
            UUID id = UUID.randomUUID();
            var provider = new PythonBridgeProvider(WebClient.builder(), props, new ObjectMapper(), () -> KEY);
            var subscription = provider.generate("cancel me", new CancellationToken(),
                    new GenerationContext("room", id, event -> { })).subscribe();
            Thread.sleep(100);
            subscription.dispose();
            assertTrue(cancelled.await(3, TimeUnit.SECONDS));
            assertEquals("Bearer " + KEY, cancelAuth.get());
            assertTrue(cancelBody.get().contains(id.toString()));
            assertFalse(cancelBody.get().contains(UUID.randomUUID().toString()));
        } finally { server.disposeNow(); }
    }

    @Test
    void ttsSendsAuthenticatedJsonAndAggregatesCompleteMp3() {
        AtomicReference<String> body = new AtomicReference<>();
        var server = HttpServer.create().host("127.0.0.1").port(0).handle((request, response) ->
                request.receive().aggregate().asString().flatMap(value -> {
                    body.set(value);
                    return response.header("Content-Type", "audio/mpeg")
                            .sendByteArray(Flux.just(new byte[]{1, 2}, new byte[]{3, 4})).then();
                })).bindNow();
        try {
            var provider = new PythonBridgeTtsProvider(WebClient.builder(), props(server, "/bridge"), () -> KEY);
            List<byte[]> audio = provider.synthesize("sprech bitte", new CancellationToken())
                    .collectList().block(Duration.ofSeconds(3));
            assertEquals(1, audio.size());
            assertArrayEquals(new byte[]{1, 2, 3, 4}, audio.getFirst());
            assertEquals("audio/mpeg", provider.mime());
            assertEquals("{\"text\":\"sprech bitte\"}", body.get());
        } finally { server.disposeNow(); }
    }

    private static JarvisProperties props(reactor.netty.DisposableServer server, String ignored) {
        JarvisProperties props = new JarvisProperties();
        props.setBridgeUrl("http://127.0.0.1:" + server.port());
        props.setBridgeToken(KEY);
        return props;
    }
}
