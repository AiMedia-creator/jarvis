package de.jarvis.backend.provider;

import de.jarvis.backend.audio.CancellationToken;
import de.jarvis.backend.config.JarvisProperties;
import java.time.Duration;
import java.util.Map;
import org.springframework.http.MediaType;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;

/** Emits one decodable MP3 per sentence, not arbitrarily truncated network chunks. */
public final class ElevenLabsTtsProvider implements TtsProvider {
    private final WebClient client;
    private final JarvisProperties props;
    private final java.util.function.Supplier<String> keySupplier;

    public ElevenLabsTtsProvider(WebClient.Builder builder, JarvisProperties props) {
        this(builder, props, () -> System.getenv("ELEVENLABS_API_KEY"));
    }

    ElevenLabsTtsProvider(WebClient.Builder builder, JarvisProperties props,
                         java.util.function.Supplier<String> keySupplier) {
        this.client = builder.codecs(codecs -> codecs.defaultCodecs().maxInMemorySize(2 * 1024 * 1024)).build();
        this.props = props;
        this.keySupplier = keySupplier;
    }

    @Override
    public Flux<byte[]> synthesize(String sentence, CancellationToken token) {
        return Flux.defer(() -> {
            token.checkpoint();
            String key = keySupplier.get();
            if (key == null || key.isBlank()) {
                return Flux.error(new IllegalStateException("ElevenLabs ist nicht konfiguriert."));
            }
            String endpoint = props.getElevenlabsUrl().replaceAll("/+$", "") + "/"
                    + props.getElevenlabsVoiceId() + "/stream?output_format=mp3_44100_128";
            return client.post().uri(endpoint).contentType(MediaType.APPLICATION_JSON)
                    .accept(MediaType.parseMediaType("audio/mpeg"))
                    .header("xi-api-key", key)
                    .bodyValue(Map.of("text", sentence, "model_id", props.getElevenlabsModel()))
                    .retrieve()
                    .onStatus(status -> status.isError(), response -> response.releaseBody()
                            .thenReturn(new IllegalStateException("ElevenLabs-Anfrage fehlgeschlagen.")))
                    .bodyToMono(byte[].class).timeout(Duration.ofSeconds(30))
                    .filter(bytes -> bytes.length > 0)
                    .switchIfEmpty(reactor.core.publisher.Mono.error(new IllegalStateException("TTS liefert kein Audio.")))
                    .filter(bytes -> !token.isCancelled()).flux();
        }).doOnCancel(token::cancel);
    }
}
