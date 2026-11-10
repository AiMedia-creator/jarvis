package de.jarvis.backend.provider;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import de.jarvis.backend.audio.CancellationToken;
import de.jarvis.backend.config.JarvisProperties;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import org.springframework.core.ParameterizedTypeReference;
import org.springframework.http.MediaType;
import org.springframework.http.codec.ServerSentEvent;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;

/** Cancellation is performed by Reactor subscription disposal, never by interrupting Netty. */
public final class AnthropicSseProvider implements GenerationProvider {
    private static final ParameterizedTypeReference<ServerSentEvent<String>> EVENT =
            new ParameterizedTypeReference<>() {};
    private final WebClient client;
    private final JarvisProperties props;
    private final ObjectMapper mapper;
    private final java.util.function.Supplier<String> keySupplier;

    public AnthropicSseProvider(WebClient.Builder builder, JarvisProperties props, ObjectMapper mapper) {
        this(builder, props, mapper, () -> System.getenv("ANTHROPIC_API_KEY"));
    }

    AnthropicSseProvider(WebClient.Builder builder, JarvisProperties props, ObjectMapper mapper,
                         java.util.function.Supplier<String> keySupplier) {
        this.client = builder.build();
        this.props = props;
        this.mapper = mapper;
        this.keySupplier = keySupplier;
    }

    @Override
    public Flux<String> generate(String prompt, CancellationToken token) {
        return Flux.defer(() -> {
            token.checkpoint();
            String key = keySupplier.get();
            if (key == null || key.isBlank()) {
                return Flux.error(new IllegalStateException("Anthropic ist nicht konfiguriert."));
            }
            return client.post().uri(props.getAnthropicUrl())
                    .contentType(MediaType.APPLICATION_JSON).accept(MediaType.TEXT_EVENT_STREAM)
                    .header("x-api-key", key).header("anthropic-version", "2023-06-01")
                    .bodyValue(Map.of("model", props.getAnthropicModel(), "max_tokens", 1024,
                            "stream", true, "system", "Du bist Jarvis. Antworte kurz und auf Deutsch.",
                            "messages", List.of(Map.of("role", "user", "content", prompt))))
                    .retrieve()
                    .onStatus(status -> status.isError(), response -> response.releaseBody()
                            .thenReturn(new IllegalStateException("Anthropic-Anfrage fehlgeschlagen.")))
                    .bodyToFlux(EVENT)
                    .<String>handle((event, sink) -> {
                        if (token.isCancelled()) { sink.complete(); return; }
                        String data = event.data();
                        if (data == null || data.isBlank() || "[DONE]".equals(data)) return;
                        try {
                            JsonNode node = mapper.readTree(data);
                            if ("error".equals(node.path("type").asText())) {
                                sink.error(new IllegalStateException("Anthropic meldet einen Streamfehler."));
                            } else if ("content_block_delta".equals(node.path("type").asText())
                                    && "text_delta".equals(node.path("delta").path("type").asText())) {
                                String text = node.path("delta").path("text").asText("");
                                if (!text.isEmpty()) sink.next(text);
                            }
                        } catch (Exception ex) {
                            sink.error(new IllegalStateException("Ungültiges Modell-Streamformat."));
                        }
                    }).timeout(Duration.ofSeconds(30));
        }).doOnCancel(token::cancel);
    }
}
