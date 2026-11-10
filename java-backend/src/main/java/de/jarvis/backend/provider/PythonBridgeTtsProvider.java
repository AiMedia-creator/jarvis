package de.jarvis.backend.provider;

import de.jarvis.backend.audio.CancellationToken;
import de.jarvis.backend.config.JarvisProperties;
import java.time.Duration;
import java.util.Map;
import java.util.function.Supplier;
import org.springframework.http.MediaType;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Flux;

/** Reactive adapter for the authenticated Python /internal/tts endpoint. */
public final class PythonBridgeTtsProvider implements TtsProvider {
    private final WebClient client;
    private final JarvisProperties props;
    private final Supplier<String> tokenSupplier;

    public PythonBridgeTtsProvider(WebClient.Builder builder, JarvisProperties props) {
        this(builder, props, props::getBridgeToken);
    }

    PythonBridgeTtsProvider(WebClient.Builder builder, JarvisProperties props, Supplier<String> tokenSupplier) {
        this.client = builder.build();
        this.props = props;
        this.tokenSupplier = tokenSupplier;
    }

    @Override
    public Flux<byte[]> synthesize(String sentence, CancellationToken token) {
        return Flux.defer(() -> {
            token.checkpoint();
            String bearer = tokenSupplier.get();
            if (bearer == null || bearer.isBlank()) {
                return Flux.error(new IllegalStateException("Python-Bridge-Token fehlt."));
            }
            return client.post()
                    .uri(props.getBridgeUrl().replaceAll("/+$", "") + "/internal/tts")
                    .contentType(MediaType.APPLICATION_JSON)
                    .accept(MediaType.parseMediaType("audio/mpeg"))
                    .headers(headers -> headers.setBearerAuth(bearer))
                    .bodyValue(Map.of("text", bounded(sentence)))
                    .retrieve()
                    .onStatus(status -> status.isError(), response -> response.releaseBody()
                            .thenReturn(new IllegalStateException("Python-TTS-Anfrage fehlgeschlagen.")))
                    .bodyToMono(byte[].class)
                    .timeout(Duration.ofSeconds(60))
                    .filter(bytes -> !token.isCancelled())
                    .flux();
        });
    }

    /**
     * Echtes Streaming: /internal/tts/stream liefert PCM-Stücke, sobald der Anbieter sie erzeugt (oder ein ganzes
     * MP3 als Rückfall). Abbruch (Token/Abo-Ende) schließt die HTTP-Verbindung; die Brücke schließt daraufhin auch
     * die Verbindung zum Anbieter. Zeitlimits: 15 s ohne neues Stück, 60 s je Satz.
     */
    @Override
    public Flux<TtsChunk> stream(String sentence, CancellationToken token) {
        return Flux.defer(() -> {
            token.checkpoint();
            String bearer = tokenSupplier.get();
            if (bearer == null || bearer.isBlank()) return Flux.error(new IllegalStateException("Python-Bridge-Token fehlt."));
            return client.post()
                    .uri(props.getBridgeUrl().replaceAll("/+$", "") + "/internal/tts/stream")
                    .contentType(MediaType.APPLICATION_JSON)
                    .headers(headers -> headers.setBearerAuth(bearer))
                    .bodyValue(Map.of("text", bounded(sentence)))
                    .exchangeToFlux(response -> {
                        String mime = response.headers().asHttpHeaders().getFirst("X-Audio-Mime");
                        if (response.statusCode().isError() || !TtsChunk.allowedMime(mime))
                            return response.releaseBody().thenMany(Flux.error(new IllegalStateException("Python-TTS-Strom fehlgeschlagen.")));
                        Flux<byte[]> bytes = response.bodyToFlux(org.springframework.core.io.buffer.DataBuffer.class)
                                .map(buffer -> {
                                    byte[] out = new byte[buffer.readableByteCount()];
                                    buffer.read(out);
                                    org.springframework.core.io.buffer.DataBufferUtils.release(buffer);
                                    return out;
                                });
                        if (!TtsChunk.isPcm(mime)) // MP3 nur am Stück dekodierbar
                            return bytes.reduce(new java.io.ByteArrayOutputStream(), (acc, b) -> { acc.writeBytes(b); return acc; })
                                    .map(acc -> new TtsChunk(mime, acc.toByteArray())).flux();
                        return alignPcm(bytes).map(b -> new TtsChunk(mime, b));
                    })
                    .timeout(Duration.ofSeconds(15))
                    .take(Duration.ofSeconds(60))
                    .takeWhile(chunk -> !token.isCancelled());
        });
    }

    /** Netzwerkstücke auf ganze 16-bit-Samples ausrichten und auf höchstens 32 KiB begrenzen. */
    static Flux<byte[]> alignPcm(Flux<byte[]> source) {
        return Flux.defer(() -> {
            byte[][] carry = {new byte[0]};
            return source.concatMap(b -> {
                byte[] joined = new byte[carry[0].length + b.length];
                System.arraycopy(carry[0], 0, joined, 0, carry[0].length);
                System.arraycopy(b, 0, joined, carry[0].length, b.length);
                int even = joined.length - (joined.length % 2);
                carry[0] = java.util.Arrays.copyOfRange(joined, even, joined.length);
                java.util.List<byte[]> parts = new java.util.ArrayList<>();
                for (int i = 0; i < even; i += 32768) parts.add(java.util.Arrays.copyOfRange(joined, i, Math.min(even, i + 32768)));
                return Flux.fromIterable(parts);
            });
        });
    }

    @Override
    public String mime() {
        return "audio/mpeg";
    }

    private static String bounded(String value) {
        if (value == null) return "";
        return value.length() > 8000 ? value.substring(0, 8000) : value;
    }
}
