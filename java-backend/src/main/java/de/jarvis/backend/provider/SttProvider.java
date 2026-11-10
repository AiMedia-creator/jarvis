package de.jarvis.backend.provider;

import com.fasterxml.jackson.databind.JsonNode;
import de.jarvis.backend.audio.CancellationToken;
import de.jarvis.backend.config.JarvisProperties;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.MediaType;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.web.reactive.function.BodyInserters;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.core.publisher.Mono;

public class SttProvider {
    private final WebClient client;
    private final JarvisProperties props;

    public SttProvider(WebClient.Builder builder, JarvisProperties props) {
        this.client = builder.build();
        this.props = props;
    }

    /** Erkannter Text plus die von der Bridge gemessenen Zeiten (null, wenn der Dienst keine liefert). */
    public record SttResult(String text, Double bridgeWaitMs, Double decodeMs) { }

    /** Die Bridge lehnt ab, weil gerade eine andere Aufnahme erkannt wird (HTTP 429). */
    public static final class SttRejected extends IllegalStateException {
        public SttRejected(String message) { super(message); }
    }

    public Mono<String> transcribe(byte[] pcm, CancellationToken token) {
        return transcribeTimed(pcm, token).map(SttResult::text);
    }

    public Mono<SttResult> transcribeTimed(byte[] pcm, CancellationToken token) {
        return Mono.defer(() -> {
            token.checkpoint();
            if (pcm.length > 1_920_000 || pcm.length < 6400 || pcm.length % 640 != 0) {
                return Mono.error(new IllegalArgumentException("PCM-Aufnahme hat ungültige Länge."));
            }
            if (props.isDemo()) return Mono.just(new SttResult("[Demo-Audio empfangen – nicht transkribiert]", null, null));
            if (props.getSttUrl() == null || props.getSttUrl().isBlank()) {
                return Mono.error(new IllegalStateException("STT benötigt JARVIS_STT_URL."));
            }
            ByteArrayResource capture = new ByteArrayResource(wav(pcm)) {
                @Override public String getFilename() { return "capture.wav"; }
            };
            var form = new LinkedMultiValueMap<String, Object>();
            form.add("file", capture);
            form.add("model", "whisper-1");
            form.add("language", "de");
            var request = client.post().uri(props.getSttUrl()).contentType(MediaType.MULTIPART_FORM_DATA);
            if (props.getSttApiKey() != null && !props.getSttApiKey().isBlank()) {
                request.header("Authorization", "Bearer " + props.getSttApiKey());
            }
            return request.body(BodyInserters.fromMultipartData(form)).retrieve()
                    .onStatus(status -> status.isError(), response -> response.releaseBody()
                            .thenReturn(response.statusCode().value() == 429
                                    ? new SttRejected(statusMessage(429))
                                    : new IllegalStateException(statusMessage(response.statusCode().value()))))
                    .bodyToMono(JsonNode.class).timeout(Duration.ofSeconds(60))
                    .map(node -> {
                        String text = node.path("text").asText("");
                        if (text.length() > 8000) throw new IllegalStateException("STT-Antwort ist zu lang.");
                        return new SttResult(text.trim(), measured(node, "bridge_wait_ms"), measured(node, "decode_ms"));
                    }).filter(result -> !token.isCancelled());
        }).doOnCancel(token::cancel);
    }

    static String statusMessage(int status) {
        if (status == 429) return "Spracherkennung ist belegt: Es wird eine Aufnahme nach der anderen erkannt.";
        if (status == 503) return "Lokales Whisper-Modell nicht verfügbar; Installation und Cache prüfen.";
        if (status == 401 || status == 403) return "Spracherkennung lehnt die Anfrage ab (Bridge-Zugang prüfen).";
        return "STT-Anfrage fehlgeschlagen (HTTP " + status + ").";
    }

    private static Double measured(JsonNode node, String field) {
        JsonNode value = node.path(field);
        if (!value.isNumber()) return null;
        double ms = value.asDouble();
        return Double.isFinite(ms) && ms >= 0 && ms < 600_000 ? ms : null;
    }

    public static byte[] wav(byte[] pcm) {
        ByteBuffer buffer = ByteBuffer.allocate(44 + pcm.length).order(ByteOrder.LITTLE_ENDIAN);
        buffer.put("RIFF".getBytes(StandardCharsets.US_ASCII)).putInt(36 + pcm.length)
                .put("WAVEfmt ".getBytes(StandardCharsets.US_ASCII)).putInt(16)
                .putShort((short) 1).putShort((short) 1).putInt(16000).putInt(32000)
                .putShort((short) 2).putShort((short) 16)
                .put("data".getBytes(StandardCharsets.US_ASCII)).putInt(pcm.length).put(pcm);
        return buffer.array();
    }
}
