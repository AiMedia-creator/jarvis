package de.jarvis.backend.provider;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import de.jarvis.backend.audio.CancellationToken;
import de.jarvis.backend.config.JarvisProperties;
import java.time.Duration;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.function.Supplier;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;

/** Internal loopback adapter. Only public token/log/memory events cross this boundary. */
public final class PythonBridgeProvider implements GenerationProvider {
    private static final ParameterizedTypeReference<String> LINES = new ParameterizedTypeReference<>() { };
    private final WebClient client;
    /** Max silence from the bridge (not from the model). Package-private for tests. */
    Duration idleTimeout = Duration.ofSeconds(30);
    private final JarvisProperties props;
    private final ObjectMapper mapper;
    private final Supplier<String> tokenSupplier;

    public PythonBridgeProvider(WebClient.Builder builder, JarvisProperties props, ObjectMapper mapper) {
        this(builder, props, mapper, props::getBridgeToken);
    }
    PythonBridgeProvider(WebClient.Builder builder, JarvisProperties props, ObjectMapper mapper,
                        Supplier<String> tokenSupplier) {
        this.client = builder.build(); this.props = props; this.mapper = mapper; this.tokenSupplier = tokenSupplier;
    }

    @Override public Flux<String> generate(String prompt, CancellationToken token) {
        return generate(prompt, token, new GenerationContext("standalone", java.util.UUID.randomUUID(), e -> { }));
    }

    @Override public Flux<String> generate(String prompt, CancellationToken token, GenerationContext context) {
        return Flux.defer(() -> {
            token.checkpoint();
            String bearer = tokenSupplier.get();
            if (bearer == null || bearer.isBlank()) return Flux.error(new IllegalStateException("Python-Bridge-Token fehlt."));
            Map<String, Object> body = Map.of("text", bounded(prompt), "conversation_id", bounded(context.roomId()),
                    "request_id", context.requestId().toString());
            return client.post().uri(props.getBridgeUrl().replaceAll("/+$", "") + "/internal/generate")
                    .contentType(MediaType.APPLICATION_JSON).accept(MediaType.parseMediaType("application/x-ndjson"))
                    .headers(h -> h.setBearerAuth(bearer)).bodyValue(body).retrieve()
                    .onStatus(s -> s.isError(), r -> r.releaseBody().thenReturn(new IllegalStateException("Python-Bridge-Anfrage fehlgeschlagen.")))
                    .bodyToFlux(String.class)
                    // Inactivity of the bridge itself: heartbeats arrive every 2 s, so long tool calls or model
                    // thinking never trip this; only a dead or stuck bridge does.
                    .timeout(idleTimeout)
                    .flatMapIterable(line -> line.lines().toList())
                    .<String>handle((line, sink) -> handleLine(line, sink, token, context));
        }).doOnCancel(() -> cancelRequest(context.requestId().toString()));
    }

    private void handleLine(String line, reactor.core.publisher.SynchronousSink<String> sink,
                            CancellationToken token, GenerationContext context) {
        if (token.isCancelled() || line == null || line.isBlank()) { if (token.isCancelled()) sink.complete(); return; }
        try {
            String trimmed = line.trim();
            if (trimmed.isEmpty() || trimmed.startsWith(":")) return;
            JsonNode node = mapper.readTree(trimmed); String type = node.path("type").asText("");
            switch (type) {
                case "token" -> { String text = node.path("text").asText(""); if (!text.isEmpty()) sink.next(text); }
                case "log" -> context.emit(event("LOG", "level", "info", "message", bounded(node.path("message").asText(""))));
                case "memory" -> {
                    Map<String, Object> event = new LinkedHashMap<>(); event.put("type", "MEMORY");
                    node.fields().forEachRemaining(entry -> { if (!"type".equals(entry.getKey())) event.put(entry.getKey(), entry.getValue()); });
                    context.emit(event);
                }
                case "tool" -> {
                    // Public tool name only (e.g. "nachrichten"); used by the UI as a structured view hint.
                    String name = node.path("name").asText("");
                    if (name.matches("[a-z0-9_]{1,64}")) context.emit(event("TOOL", "name", name, "level", "info"));
                }
                case "heartbeat", "ping" -> { }
                case "done" -> sink.complete();
                case "error" -> sink.error(new IllegalStateException("Python-Bridge meldet einen Fehler."));
                default -> { }
            }
        } catch (Exception ignored) { sink.error(new IllegalStateException("Ungültiges Python-Bridge-Streamformat.")); }
    }

    private void cancelRequest(String requestId) {
        String bearer = tokenSupplier.get(); if (bearer == null || bearer.isBlank()) return;
        client.post().uri(props.getBridgeUrl().replaceAll("/+$", "") + "/internal/cancel")
                .contentType(MediaType.APPLICATION_JSON).headers(h -> h.setBearerAuth(bearer))
                .bodyValue(Map.of("request_id", requestId)).retrieve().bodyToMono(Void.class)
                .onErrorResume(error -> Mono.empty()).subscribe();
    }

    public Mono<Map<String, Object>> snapshot(String query, int limit) { return memory("GET", "/internal/memory?q=" + encode(query) + "&limit=" + Math.min(50, Math.max(1, limit)), null); }
    public Mono<Map<String, Object>> remember(String content, String category) { return memory("POST", "/internal/memory", Map.of("content", bounded(content), "category", bounded(category))); }
    public Mono<Map<String, Object>> forget(String id) {
        return memory("DELETE", "/internal/memory/" + encodePathSegment(id), null);
    }
    public Mono<Map<String, Object>> news(String rubrik, String keyword) {
        return memory("GET", "/internal/news?rubrik=" + encode(rubrik) + "&stichwort=" + encode(keyword), null);
    }
    public Mono<Map<String, Object>> market(String query) {
        return memory("GET", "/internal/market?q=" + encode(query), null);
    }
    public Mono<Map<String, Object>> marketDirectory() {
        return memory("GET", "/internal/market?verzeichnis=1", null);
    }
    /** Kommandozentrale: Aufruf mit durchgereichtem Statuscode (z. B. 409 Konflikt), damit die UI zurückrollen kann. */
    public Mono<org.springframework.http.ResponseEntity<Map<String, Object>>> command(String method, String path, Object body) {
        String bearer = tokenSupplier.get(); if (bearer == null || bearer.isBlank()) return Mono.error(new IllegalStateException("Python-Bridge-Token fehlt."));
        WebClient.RequestBodySpec request = client.method(org.springframework.http.HttpMethod.valueOf(method)).uri(java.net.URI.create(props.getBridgeUrl().replaceAll("/+$", "") + path))
                .headers(h -> h.setBearerAuth(bearer));
        if (body != null) request.contentType(MediaType.APPLICATION_JSON);
        return (body == null ? request : request.bodyValue(body)).exchangeToMono(response -> response
                .bodyToMono(new ParameterizedTypeReference<Map<String, Object>>() {})
                .defaultIfEmpty(Map.of())
                .map(value -> org.springframework.http.ResponseEntity.status(response.statusCode()).body(value)));
    }
    public static String query(String value) { return encode(value); }
    private Mono<Map<String, Object>> memory(String method, String path, Object body) {
        String bearer = tokenSupplier.get(); if (bearer == null || bearer.isBlank()) return Mono.error(new IllegalStateException("Python-Bridge-Token fehlt."));
        WebClient.RequestBodySpec request = client.method(org.springframework.http.HttpMethod.valueOf(method)).uri(java.net.URI.create(props.getBridgeUrl().replaceAll("/+$", "") + path))
                .headers(h -> h.setBearerAuth(bearer));
        if (body != null) request.contentType(MediaType.APPLICATION_JSON);
        return (body == null ? request : request.bodyValue(body)).retrieve().bodyToMono(new ParameterizedTypeReference<>() {});
    }
    private static String bounded(String value) { return value == null ? "" : value.length() > 8000 ? value.substring(0, 8000) : value; }
    private static String encode(String value) {
        return java.net.URLEncoder.encode(value == null ? "" : value, java.nio.charset.StandardCharsets.UTF_8);
    }
    private static String encodePathSegment(String value) {
        return encode(value).replace("+", "%20");
    }
    private static Map<String, Object> event(String type, String k1, Object v1, String k2, Object v2) { Map<String,Object> m=new LinkedHashMap<>(); m.put("type",type);m.put(k1,v1);m.put(k2,v2);return m; }
}
