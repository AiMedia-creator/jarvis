package de.jarvis.backend.room;

import com.fasterxml.jackson.databind.ObjectMapper;
import de.jarvis.backend.config.JarvisProperties;
import de.jarvis.backend.provider.GenerationProvider;
import de.jarvis.backend.provider.SttProvider;
import de.jarvis.backend.provider.TtsProvider;
import de.jarvis.backend.protocol.Protocol;
import java.util.List;
import java.util.concurrent.ConcurrentHashMap;

/** Thread-safe bounded registry; rooms live while at least one websocket client is present. */
public final class RoomRegistry {
    private final ConcurrentHashMap<String, Room> rooms = new ConcurrentHashMap<>();
    private final JarvisProperties props;
    private final GenerationProvider generation;
    private final TtsProvider tts;
    private final SttProvider stt;
    private final ObjectMapper mapper;

    /** Compatibility constructor for existing focused tests. */
    public RoomRegistry(JarvisProperties props, GenerationProvider generation,
                        TtsProvider tts, ObjectMapper mapper) {
        this(props, generation, tts, null, mapper);
    }

    public RoomRegistry(JarvisProperties props, GenerationProvider generation,
                        TtsProvider tts, SttProvider stt, ObjectMapper mapper) {
        this.props = props;
        this.generation = generation;
        this.tts = tts;
        this.stt = stt;
        this.mapper = mapper;
    }

    public synchronized Room getOrCreate(String id) {
        if (!Protocol.validId(id)) throw new IllegalArgumentException("ungültige room id");
        Room existing = rooms.get(id);
        if (existing != null) return existing;
        if (rooms.size() >= props.getMaxRooms()) throw new IllegalStateException("zu viele Räume");
        Room candidate = new Room(id, props.getMaxClientsPerRoom(), props.getMaxOutputFrames(),
                props.getMaxInputChars(), props.isDemo(), generation, tts, stt, mapper,
                Protocol.defaultCities(), camerasFromProperties());
        Room winner = rooms.putIfAbsent(id, candidate);
        return winner == null ? candidate : winner;
    }

    public int size() { return rooms.size(); }

    public void removeIfEmpty(String id) {
        Room room = rooms.get(id);
        if (room != null && room.clientCount() == 0) rooms.remove(id, room);
    }

    private List<Protocol.Camera> camerasFromProperties() {
        return props.getCameras();
    }
}
