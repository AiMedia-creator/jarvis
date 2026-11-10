package de.jarvis.backend.web;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import de.jarvis.backend.audio.AudioPacketCodec;
import de.jarvis.backend.config.JarvisProperties;
import de.jarvis.backend.room.Room;
import de.jarvis.backend.room.RoomRegistry;
import de.jarvis.backend.security.SessionService;
import java.io.ByteArrayOutputStream;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import org.springframework.web.reactive.socket.WebSocketHandler;
import org.springframework.web.reactive.socket.WebSocketMessage;
import org.springframework.web.reactive.socket.WebSocketSession;
import reactor.core.publisher.Mono;

public final class AudioWebSocketHandler implements WebSocketHandler {
    private static final int MAX_JSON_BYTES = 16_384;
    private static final int PACKET_BYTES = 640;
    private static final int MAX_CAPTURE_BYTES = 1_920_000;   // 60 s Sprache
    private static final int MAX_PACKETS_PER_SECOND = 100;
    private static final long MAX_CAPTURE_NANOS = 61_000_000_000L;
    /** Erst bei grobem Missbrauch wird die Verbindung getrennt; normale Ausreißer verwerfen nur die Aufnahme. */
    static final int ABUSE_MESSAGES_PER_SECOND = 1_000;
    static final int SOFT_MESSAGES_PER_SECOND = 110;

    private final RoomRegistry rooms;
    private final AudioPacketCodec codec;
    private final SessionService auth;
    private final ObjectMapper mapper;
    private final int vadThreshold;

    public AudioWebSocketHandler(RoomRegistry rooms, AudioPacketCodec codec, SessionService auth) {
        this(rooms, codec, auth, new ObjectMapper());
    }

    public AudioWebSocketHandler(RoomRegistry rooms, AudioPacketCodec codec, SessionService auth, ObjectMapper mapper) {
        this(rooms, codec, auth, mapper, 492);
    }
    public AudioWebSocketHandler(RoomRegistry rooms, AudioPacketCodec codec, SessionService auth,
                                 ObjectMapper mapper, JarvisProperties props) {
        this(rooms, codec, auth, mapper, props.getVadThreshold());
    }
    private AudioWebSocketHandler(RoomRegistry rooms, AudioPacketCodec codec, SessionService auth,
                                  ObjectMapper mapper, int vadThreshold) {
        this.rooms = rooms; this.codec = codec; this.auth = auth; this.mapper = mapper; this.vadThreshold = vadThreshold;
    }

    @Override
    public Mono<Void> handle(WebSocketSession session) {
        URI uri = session.getHandshakeInfo().getUri();
        if (!auth.originAllowed(session.getHandshakeInfo().getHeaders().getOrigin())
                || !auth.authorized(session.getHandshakeInfo().getHeaders().getFirst("Cookie"))) {
            return session.close();
        }
        String roomId = pathRoom(uri);
        String client = query(uri, "client");
        if (roomId == null || !de.jarvis.backend.protocol.Protocol.validId(client)) return session.close();

        Room room;
        try { room = rooms.getOrCreate(roomId); }
        catch (RuntimeException error) { return session.close(); }
        if (!room.claimAudio(client)) return session.close();

        AtomicReference<Capture> capture = new AtomicReference<>();
        AtomicBoolean released = new AtomicBoolean();
        long[] rate = { System.nanoTime(), 0 };
        Mono<Void> inbound = session.receive()
                .concatMap(message -> {
                    long now = System.nanoTime();
                    if (now - rate[0] > 1_000_000_000L) { rate[0] = now; rate[1] = 0; }
                    if (++rate[1] > ABUSE_MESSAGES_PER_SECOND) return Mono.error(new IllegalStateException("Audio-Rate-Limit"));
                    // Ein Paketschwall (z. B. nach einem blockierten Browser-Tab) kostet nur die laufende Aufnahme,
                    // nie die ganze Sprachsitzung: früher schloss jeder Protokollfehler Audio- und Steuerkanal.
                    if (rate[1] > SOFT_MESSAGES_PER_SECOND)
                        return abortCapture(room, client, capture, "Zu viele Audiopakete auf einmal – Aufnahme verworfen, bitte noch einmal sprechen.");
                    return receiveOne(room, client, capture, message)
                            .onErrorResume(error -> abortCapture(room, client, capture, safe(error.getMessage())));
                })
                .then()
                .doFinally(signal -> release(room, client, capture, released));
        Mono<Void> outbound = session.send(room.audioFlux()
                .map(frame -> session.binaryMessage(factory -> factory.wrap(codec.encode(frame)))));
        return Mono.firstWithSignal(outbound, inbound)
                .doFinally(signal -> release(room, client, capture, released));
    }

    private Mono<Void> receiveOne(Room room, String client, AtomicReference<Capture> capture,
                                   WebSocketMessage message) {
        if (message.getType() == WebSocketMessage.Type.TEXT) {
            String text = message.getPayloadAsText();
            if (text.getBytes(StandardCharsets.UTF_8).length > MAX_JSON_BYTES) return Mono.error(new IllegalArgumentException("Nachricht ist zu groß"));
            try {
                Map<String, Object> body = mapper.readValue(text, new TypeReference<>() { });
                return dispatchText(room, client, capture, body);
            } catch (Exception error) {
                return Mono.error(new IllegalArgumentException(safe(error.getMessage())));
            }
        }
        if (message.getType() != WebSocketMessage.Type.BINARY) return Mono.empty();
        Capture active = capture.get();
        if (active == null) return Mono.error(new IllegalArgumentException("AUDIO_START fehlt"));
        byte[] pcm = bytes(message.getPayload().asByteBuffer());
        if (!AudioPacketCodec.validPcmPacket(pcm) || pcm.length != PACKET_BYTES) {
            return Mono.error(new IllegalArgumentException("PCM muss aus 640 Bytes pro 20-ms-Paket bestehen"));
        }
        if (!active.allowPacket()) return Mono.error(new IllegalArgumentException("Zu viele Audiopakete pro Sekunde"));
        if (active.elapsedNanos() > MAX_CAPTURE_NANOS) return Mono.error(new IllegalArgumentException("Aufnahme ist zu lang"));
        if (active.size() + pcm.length > MAX_CAPTURE_BYTES) return Mono.error(new IllegalArgumentException("Aufnahme ist zu lang"));
        active.append(pcm, vadThreshold);
        return Mono.empty();
    }

    private Mono<Void> dispatchText(Room room, String client, AtomicReference<Capture> capture,
                                    Map<String, Object> body) {
        if (body == null || body.size() > 8 || !(body.get("type") instanceof String type)) {
            return Mono.error(new IllegalArgumentException("type fehlt"));
        }
        return switch (type) {
            case "AUDIO_START" -> {
                UUID id = uuid(body.get("utterance_id"));
                Object rate = body.get("sample_rate");
                if (!(rate instanceof Number n) || !Double.isFinite(n.doubleValue()) || n.doubleValue() != 16_000d)
                    throw new IllegalArgumentException("sample_rate muss 16000 sein");
                Capture previous = capture.getAndSet(null);
                if (previous != null) room.audioAbort(client, previous.id);
                room.audioStart(client, id, 16_000);
                capture.set(new Capture(id));
                yield Mono.empty();
            }
            case "AUDIO_END" -> {
                UUID id = uuid(body.get("utterance_id"));
                Capture active = capture.get();
                if (active == null || !active.id.equals(id)) throw new IllegalArgumentException("unbekannte utterance_id");
                if (active.size() < 6_400) throw new IllegalArgumentException("Aufnahme muss mindestens 200 ms lang sein");
                if (!active.voiced()) {
                    capture.compareAndSet(active, null);
                    room.audioRejected(client, id, "Keine Sprache erkannt (stille Aufnahme).");
                    yield Mono.empty();
                }
                capture.compareAndSet(active, null);
                try {
                    room.transcribe(client, id, active.bytes());
                } catch (RuntimeException error) {
                    // z. B. STT nicht konfiguriert: sichtbar melden, Sitzung bleibt bestehen.
                    room.audioRejected(client, id, safe(error.getMessage()));
                }
                yield Mono.empty();
            }
            case "AUDIO_ABORT" -> {
                UUID id = uuid(body.get("utterance_id"));
                Capture active = capture.get();
                if (active != null && active.id.equals(id)) {
                    capture.compareAndSet(active, null);
                    room.audioAbort(client, id);
                }
                yield Mono.empty();
            }
            default -> Mono.error(new IllegalArgumentException("Unbekannter Audiobefehl"));
        };
    }

    /** Verwirft nur die laufende Aufnahme und meldet den Grund (ERROR, Stufe audio); die Verbindung bleibt offen. */
    private Mono<Void> abortCapture(Room room, String client, AtomicReference<Capture> capture, String reason) {
        Capture active = capture.getAndSet(null);
        if (active != null) {
            try { room.audioRejected(client, active.id, reason); } catch (RuntimeException ignored) { }
        }
        return Mono.empty();
    }

    private void release(Room room, String client, AtomicReference<Capture> capture, AtomicBoolean released) {
        if (released.compareAndSet(false, true)) {
            Capture active = capture.getAndSet(null);
            if (active != null) {
                try { room.audioAbort(client, active.id); } catch (RuntimeException ignored) { }
            }
            room.releaseAudio(client);
        }
    }

    private static byte[] bytes(ByteBuffer buffer) {
        ByteBuffer copy = buffer.slice();
        byte[] result = new byte[copy.remaining()];
        copy.get(result);
        return result;
    }

    private static UUID uuid(Object raw) {
        if (!(raw instanceof String text)) throw new IllegalArgumentException("utterance_id fehlt");
        try { return UUID.fromString(text); }
        catch (IllegalArgumentException error) { throw new IllegalArgumentException("utterance_id ungültig"); }
    }

    private static String pathRoom(URI uri) {
        String[] parts = uri.getPath().split("/");
        if (parts.length != 4 || !"ws".equals(parts[1]) || !"audio".equals(parts[3])) return null;
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
        return de.jarvis.backend.protocol.Protocol.safe(value.replaceAll("[\\r\\n\\t]", " "), 160);
    }

    private static final class Capture {
        private final UUID id;
        private final ByteArrayOutputStream data = new ByteArrayOutputStream(MAX_CAPTURE_BYTES);
        private final long startedAt = System.nanoTime();
        private long window = System.nanoTime();
        private int packets;
        private int voicedPackets;
        private Capture(UUID id) { this.id = id; }
        private synchronized boolean allowPacket() {
            long now = System.nanoTime();
            if (now - window >= 1_000_000_000L) { window = now; packets = 0; }
            return ++packets <= MAX_PACKETS_PER_SECOND;
        }
        private synchronized void append(byte[] bytes, int threshold) {
            int crossings = 0; int previous = 0; long sum = 0;
            for (int i = 0; i + 1 < bytes.length; i += 2) {
                int sample = (short) (((bytes[i + 1] & 0xff) << 8) | (bytes[i] & 0xff));
                sum += (long) sample * sample;
                if (i > 0 && ((sample < 0) != (previous < 0))) crossings++;
                previous = sample;
            }
            double rms = Math.sqrt(sum / Math.max(1, bytes.length / 2));
            if (rms >= threshold && crossings > 0) voicedPackets++;
            data.writeBytes(bytes);
        }
        private synchronized int size() { return data.size(); }
        private synchronized byte[] bytes() { return data.toByteArray(); }
        private synchronized boolean voiced() { return voicedPackets >= 3; }
        private long elapsedNanos() { return System.nanoTime() - startedAt; }
    }
}
