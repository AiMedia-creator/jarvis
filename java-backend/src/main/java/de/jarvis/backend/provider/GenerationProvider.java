package de.jarvis.backend.provider;

import de.jarvis.backend.audio.CancellationToken;
import reactor.core.publisher.Flux;

public interface GenerationProvider {
    Flux<String> generate(String prompt, CancellationToken token);

    /** New callers provide conversation identity; old providers/tests remain source compatible. */
    default Flux<String> generate(String prompt, CancellationToken token, GenerationContext context) {
        return generate(prompt, token);
    }
}
