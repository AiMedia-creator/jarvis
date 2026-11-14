package de.jarvis.backend.web;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import de.jarvis.backend.protocol.Protocol;
import de.jarvis.backend.room.Room;
import de.jarvis.backend.room.RoomRegistry;
import de.jarvis.backend.security.SessionService;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.concurrent.atomic.AtomicBoolean;
import org.springframework.web.reactive.socket.WebSocketHandler;
import org.springframework.web.reactive.socket.WebSocketMessage;
import org.springframework.web.reactive.socket.WebSocketSession;
import reactor.core.publisher.Mono;

public final class ControlWebSocketHandler implements WebSocketHandler {
    private static final int MAX_JSON_BYTES = 16_384;
    private final RoomRegistry rooms;
    private final ObjectMapper mapper;
    private final SessionService auth;

    public ControlWebSocketHandler(RoomRegistry rooms, ObjectMapper mapper, SessionService auth) {
        this.rooms = rooms; this.mapper = mapper; this.auth = auth;
    }

    @Override
    public Mono<Void> handle(WebSocketSession session) {
        URI uri = session.getHandshakeInfo().getUri();
        if (!auth.originAllowed(session.getHandshakeInfo().getHeaders().getOrigin())
                || !auth.authorized(session.getHandshakeInfo().getHeaders().getFirst("Cookie"))) {
            return session.close();
        }
        String roomId = pathRoom(uri, "control");
        String client = query(uri, "client");
        String screen = query(uri, "screen");
        if (roomId == null || !Protocol.validId(client) || !("main".equalsIgnoreCase(screen) || "ops".equalsIgnoreCase(screen))) {
            return session.close();
        }

        Room room;
        Room.Client joined;
        try {
            room = rooms.getOrCreate(roomId);
            joined = room.join(client, screen, auth.sessionKey(session.getHandshakeInfo().getHeaders().getFirst("Cookie")));
        } catch (RuntimeException error) {
            return session.close();
        }

        AtomicBoolean left = new AtomicBoolean();
        long[] rate = { System.nanoTime(), 0 };
        Mono<Void> inbound = session.receive()
                .concatMap(message -> {
                    long now = System.nanoTime();
                    if (now - rate[0] > 1_000_000_000L) { rate[0] = now; rate[1] = 0; }
                    if (++rate[1] > 60) return Mono.error(new IllegalStateException("Rate-Limit"));
                    return receiveOne(room, joined, message);
                })
                .then()
                .doFinally(signal -> leaveOnce(room, roomId, client, left));
        Mono<Void> outbound = session.send(room.controlFlux(joined).map(session::textMessage));
        return Mono.firstWithSignal(outbound, inbound)
                .doFinally(signal -> leaveOnce(room, roomId, client, left));
    }

    private Mono<Void> receiveOne(Room room, Room.Client client, WebSocketMessage message) {
        if (message.getType() != WebSocketMessage.Type.TEXT) return Mono.empty();
        String payload = message.getPayloadAsText();
        if (payload.getBytes(StandardCharsets.UTF_8).length > MAX_JSON_BYTES) {
            sendError(client, "Nachricht ist zu groß");
            return Mono.empty();
        }
        try {
            Map<String, Object> body = mapper.readValue(payload, new TypeReference<>() { });
            dispatch(room, client, body);
        } catch (RuntimeException | java.io.IOException error) {
            sendError(client, safe(error.getMessage()));
        }
        return Mono.empty();
    }

    private void dispatch(Room room, Room.Client client, Map<String, Object> message) {
        if (message == null || message.size() > 32) throw new IllegalArgumentException("Ungültige Nachricht");
        Object rawType = message.get("type");
        if (!(rawType instanceof String type) || type.length() > 32) throw new IllegalArgumentException("type fehlt");
        if (!"PING".equals(type) && !room.isOwner(client.id()))
            throw new IllegalStateException("Nur der Besitzer darf diesen Raum steuern");
        switch (type) {
            case "PING" -> {
                Object raw = message.get("sent_at");
                if (!(raw instanceof Number number) || !Double.isFinite(number.doubleValue())) throw new IllegalArgumentException("sent_at fehlt");
                room.ping(client, number.doubleValue());
            }
            case "USER_TEXT" -> {
                if (!(message.get("text") instanceof String text)) throw new IllegalArgumentException("text fehlt");
                room.start(client.id(), text);
            }
            case "STOP_STREAM", "INTERRUPT" -> {
                Object rawId = message.get("stream_id");
                if (rawId != null && !(rawId instanceof String)) throw new IllegalArgumentException("stream_id ungültig");
                String reason = String.valueOf(message.getOrDefault("reason", "manual"));
                if (!(reason.equals("vad") || reason.equals("manual") || reason.equals("new_turn"))) throw new IllegalArgumentException("reason ungültig");
                room.stop(client.id(), (String) rawId, reason);
            }
            case "VIEW_SET" -> room.view(client, message);
            case "CAMERA_SELECT" -> room.camera(client, message);
            case "RESET" -> room.reset(client.id());
            default -> throw new IllegalArgumentException("Unbekannter Steuerbefehl");
        }
    }

    private void sendError(Room.Client client, String message) {
        client.control().tryEmitNext(jsonError(message));
    }

    private String jsonError(String message) {
        try { return mapper.writeValueAsString(Map.of("type", "ERROR", "message", safe(message))); }
        catch (Exception ignored) { return "{\"type\":\"ERROR\",\"message\":\"Ungültige Nachricht\"}"; }
    }

    private void leaveOnce(Room room, String roomId, String client, AtomicBoolean left) {
        if (left.compareAndSet(false, true)) {
            room.leave(client);
            rooms.removeIfEmpty(roomId);
        }
    }

    private static String pathRoom(URI uri, String endpoint) {
        String[] parts = uri.getPath().split("/");
        if (parts.length != 4 || !"ws".equals(parts[1]) || !endpoint.equals(parts[3])) return null;
        return parts[2];
    }

    private static String query(URI uri, String key) {
        String raw = uri.getRawQuery();
        if (raw == null) return "";
        for (String pair : raw.split("&", -1)) {
            String[] bits = pair.split("=", 2);
            if (bits.length == 2 && bits[0].equals(key)) return URLDecoder.decode(bits[1], StandardCharsets.UTF_8);
        }
        return "";
    }

    private static String safe(String value) {
        if (value == null || value.isBlank()) return "Ungültige Nachricht";
        return Protocol.safe(value.replaceAll("[\\r\\n\\t]", " "), 160);
    }
}
