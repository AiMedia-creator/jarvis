package de.jarvis.backend.security;

import de.jarvis.backend.config.JarvisProperties;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.time.Instant;
import java.util.Base64;
import java.util.HashMap;
import org.springframework.http.ResponseCookie;
import org.springframework.stereotype.Service;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;

@Service
public final class SessionService {
    private final JarvisProperties props;
    private final HashMap<String, Instant> sessions = new HashMap<>();
    private final SecureRandom random = new SecureRandom();
    private long loginWindow = System.nanoTime();
    private int attempts;

    public SessionService(JarvisProperties props) { this.props = props; }
    public boolean originAllowed(String origin) {
        return origin != null && props.getAllowedOrigins().contains(origin);
    }
    public boolean authorized(ServerWebExchange exchange) {
        return authorized(exchange.getRequest().getHeaders().getFirst("Cookie"));
    }
    public synchronized boolean authorized(String cookie) {
        if (!props.isAuthRequired()) return true;
        if (cookie == null || cookie.length() > 8192) return false;
        for (String part : cookie.split(";")) {
            String value = part.trim();
            if (value.startsWith("JARVIS_SESSION=")) {
                Instant expires = sessions.get(value.substring("JARVIS_SESSION=".length()));
                return expires != null && expires.isAfter(Instant.now());
            }
        }
        return false;
    }
    public Mono<ResponseCookie> create(String token) {
        return Mono.defer(() -> createNow(token));
    }
    private synchronized Mono<ResponseCookie> createNow(String token) {
        long nowNanos = System.nanoTime();
        if (nowNanos - loginWindow > 60_000_000_000L) { loginWindow = nowNanos; attempts = 0; }
        if (++attempts > 20 || token == null || token.length() > 1024 || props.getSessionToken().isBlank())
            return Mono.empty();
        if (!MessageDigest.isEqual(token.getBytes(StandardCharsets.UTF_8),
                props.getSessionToken().getBytes(StandardCharsets.UTF_8))) return Mono.empty();
        Instant now = Instant.now();
        sessions.entrySet().removeIf(entry -> !entry.getValue().isAfter(now));
        if (sessions.size() >= 128) return Mono.empty();
        byte[] bytes = new byte[32];
        random.nextBytes(bytes);
        String id = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
        sessions.put(id, now.plus(props.sessionTtl()));
        return Mono.just(ResponseCookie.from("JARVIS_SESSION", id).httpOnly(true)
                .secure(props.isSecureCookie()).sameSite("Strict").path("/").maxAge(props.sessionTtl()).build());
    }
}
