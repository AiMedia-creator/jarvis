package de.jarvis.backend.provider;

import de.jarvis.backend.audio.CancellationToken;
import reactor.core.publisher.Flux;

@FunctionalInterface
public interface TtsProvider {
    Flux<byte[]> synthesize(String sentence, CancellationToken token);

    default String mime() {
        return "audio/mpeg";
    }

    /** Vertonung als Strom von Stücken; Standard: ein ganzes Stück im festen Format (kein echtes Streaming). */
    default Flux<TtsChunk> stream(String sentence, CancellationToken token) {
        String format = mime();
        return synthesize(sentence, token).map(bytes -> new TtsChunk(format, bytes));
    }
}
