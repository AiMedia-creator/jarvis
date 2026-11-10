package de.jarvis.backend.web;

import com.fasterxml.jackson.databind.ObjectMapper;
import de.jarvis.backend.audio.AudioPacketCodec;
import de.jarvis.backend.config.JarvisProperties;
import de.jarvis.backend.provider.GenerationProvider;
import de.jarvis.backend.provider.SttProvider;
import de.jarvis.backend.provider.TtsProvider;
import de.jarvis.backend.room.RoomRegistry;
import de.jarvis.backend.security.SessionService;
import java.util.Map;
import java.util.regex.Pattern;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.web.reactive.HandlerMapping;
import org.springframework.web.reactive.handler.SimpleUrlHandlerMapping;
import org.springframework.web.reactive.socket.WebSocketHandler;
import org.springframework.web.reactive.socket.server.support.WebSocketHandlerAdapter;

@Configuration
public class WebSocketConfig {
    private static final Pattern CONTROL_OR_AUDIO = Pattern.compile("^/ws/[^/]+/(control|audio)$");

    @Bean
    AudioPacketCodec audioPacketCodec(ObjectMapper mapper) {
        return new AudioPacketCodec(mapper);
    }

    @Bean
    RoomRegistry roomRegistry(JarvisProperties props, GenerationProvider generation,
                              TtsProvider tts, SttProvider stt, ObjectMapper mapper) {
        return new RoomRegistry(props, generation, tts, stt, mapper);
    }

    @Bean
    WebSocketHandler wsDispatcher(RoomRegistry rooms, ObjectMapper mapper, SessionService auth,
                                  AudioPacketCodec codec, JarvisProperties props) {
        ControlWebSocketHandler control = new ControlWebSocketHandler(rooms, mapper, auth);
        AudioWebSocketHandler audio = new AudioWebSocketHandler(rooms, codec, auth, mapper, props);
        return session -> {
            String path = session.getHandshakeInfo().getUri().getPath();
            if (!CONTROL_OR_AUDIO.matcher(path).matches()) return session.close();
            return path.endsWith("/audio") ? audio.handle(session) : control.handle(session);
        };
    }

    @Bean
    HandlerMapping webSocketMapping(WebSocketHandler handler) {
        SimpleUrlHandlerMapping mapping = new SimpleUrlHandlerMapping();
        mapping.setOrder(-1);
        mapping.setUrlMap(Map.of("/ws/**", handler));
        return mapping;
    }

    @Bean
    WebSocketHandlerAdapter webSocketHandlerAdapter() {
        return new WebSocketHandlerAdapter();
    }
}
