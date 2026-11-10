package de.jarvis.backend.security;

import org.springframework.core.Ordered;
import org.springframework.core.annotation.Order;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Component;
import org.springframework.web.server.ServerWebExchange;
import org.springframework.web.server.WebFilter;
import org.springframework.web.server.WebFilterChain;
import reactor.core.publisher.Mono;

@Component
@Order(Ordered.HIGHEST_PRECEDENCE)
public final class SecurityWebFilter implements WebFilter {
    private final SessionService sessions;
    public SecurityWebFilter(SessionService sessions) { this.sessions = sessions; }

    @Override
    public Mono<Void> filter(ServerWebExchange exchange, WebFilterChain chain) {
        String path = exchange.getRequest().getPath().value();
        boolean websocket = path.startsWith("/ws/");
        boolean metrics = path.startsWith("/actuator/") && !path.equals("/actuator/health");
        if (websocket && !sessions.originAllowed(exchange.getRequest().getHeaders().getOrigin())) {
            exchange.getResponse().setStatusCode(HttpStatus.FORBIDDEN);
            return exchange.getResponse().setComplete();
        }
        if ((websocket || metrics) && !sessions.authorized(exchange)) {
            exchange.getResponse().setStatusCode(HttpStatus.UNAUTHORIZED);
            return exchange.getResponse().setComplete();
        }
        exchange.getResponse().getHeaders().set("X-Content-Type-Options", "nosniff");
        exchange.getResponse().getHeaders().set("Referrer-Policy", "same-origin");
        return chain.filter(exchange);
    }
}
