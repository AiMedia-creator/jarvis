package de.jarvis.backend.provider;

import java.util.Map;
import java.util.Objects;
import java.util.UUID;
import java.util.function.Consumer;

/** Identity and public-event sink for one generation; never carries private reasoning. */
public record GenerationContext(String roomId, UUID requestId, Consumer<Map<String, Object>> eventConsumer) {
    public GenerationContext {
        roomId = Objects.requireNonNull(roomId, "roomId");
        requestId = Objects.requireNonNull(requestId, "requestId");
        eventConsumer = eventConsumer == null ? event -> { } : eventConsumer;
    }
    public void emit(Map<String, Object> event) {
        if (event == null || event.containsKey("thinking") || event.containsKey("thought")) return;
        try { eventConsumer.accept(Map.copyOf(event)); } catch (RuntimeException ignored) { }
    }
}
