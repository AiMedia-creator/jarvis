package de.jarvis.backend.provider;

import de.jarvis.backend.audio.CancellationToken;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.time.Duration;
import java.util.List;
import reactor.core.publisher.Flux;

public final class DemoProviders {
    private DemoProviders() {}

    public static GenerationProvider generation() {
        return (prompt, token) -> Flux.fromIterable(List.of(
                "Demo-Modus aktiv. ", "Die Anfrage wurde empfangen: ",
                prompt.substring(0, Math.min(120, prompt.length())),
                ". Java liefert hier absichtlich keine echten Tool-Ergebnisse."))
                .delayElements(Duration.ofMillis(150)).filter(part -> !token.isCancelled());
    }

    public static TtsProvider tts() {
        return new TtsProvider() {
            @Override public String mime() { return "audio/wav"; }
            @Override public Flux<byte[]> synthesize(String sentence, CancellationToken token) {
                return Flux.defer(() -> {
                    token.checkpoint();
                    int samples = Math.min(16000, Math.max(3200, sentence.length() * 90));
                    byte[] pcm = new byte[samples * 2];
                    ByteBuffer buffer = ByteBuffer.wrap(pcm).order(ByteOrder.LITTLE_ENDIAN);
                    for (int i = 0; i < samples; i++) {
                        buffer.putShort((short) (Math.sin(i * 2 * Math.PI * 440 / 16000) * 1500));
                    }
                    return Flux.just(SttProvider.wav(pcm));
                });
            }
        };
    }
}
