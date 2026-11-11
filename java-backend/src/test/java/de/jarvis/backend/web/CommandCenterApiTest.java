package de.jarvis.backend.web;

import static org.junit.jupiter.api.Assertions.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import de.jarvis.backend.config.JarvisProperties;
import de.jarvis.backend.provider.PythonBridgeProvider;
import de.jarvis.backend.security.SessionService;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.test.web.reactive.server.WebTestClient;
import org.springframework.web.reactive.function.client.WebClient;
import reactor.netty.DisposableServer;
import reactor.netty.http.server.HttpServer;

/** Kommandozentrale: Schreibzugriffe nur mit Session, Origin und X-Jarvis; Statuscodes (409) kommen durch. */
class CommandCenterApiTest {
    private DisposableServer bridgeServer;
    private final List<String> calls = new CopyOnWriteArrayList<>();
    private WebTestClient web;

    @BeforeEach
    void setUp() {
        bridgeServer = HttpServer.create().host("127.0.0.1").port(0).handle((request, response) -> {
            calls.add(request.method().name() + " " + request.uri());
            if (request.uri().startsWith("/internal/anhaenge") && request.method().name().equals("POST"))
                return request.receive().aggregate().asByteArray().defaultIfEmpty(new byte[0]).flatMap(bytes -> {
                    calls.add("BYTES " + bytes.length + " " + request.requestHeaders().get("Content-Type"));
                    return response.header("Content-Type", "application/json")
                            .sendString(reactor.core.publisher.Mono.just("{\"anhang\":{\"id\":\"anh_x\"}}")).then();
                });
            if (request.uri().startsWith("/internal/orders/A-konflikt"))
                return response.status(409).header("Content-Type", "application/json")
                        .sendString(reactor.core.publisher.Mono.just("{\"fehler\":\"geändert\",\"aktuell\":{\"version\":7}}")).then();
            return response.header("Content-Type", "application/json")
                    .sendString(reactor.core.publisher.Mono.just("{\"ok\":true}")).then();
        }).bindNow();
        JarvisProperties props = new JarvisProperties();
        props.setBridgeUrl("http://127.0.0.1:" + bridgeServer.port());
        props.setAuthRequired(false);
        props.setBridgeToken("unit-test-bridge-key");
        var bridge = new PythonBridgeProvider(WebClient.builder(), props, new ObjectMapper());
        web = WebTestClient.bindToController(new ApiController(props, new SessionService(props), bridge)).build();
    }

    @AfterEach
    void tearDown() { bridgeServer.disposeNow(); }

    @Test
    void lesenGehtSchreibenNurMitOriginUndHeader() {
        web.get().uri("/api/orders").exchange().expectStatus().isOk();
        web.patch().uri("/api/orders/A-123").bodyValue(Map.of("version", 1)).exchange().expectStatus().isForbidden();
        web.patch().uri("/api/orders/A-123").header("Origin", "http://localhost:8080").bodyValue(Map.of("version", 1))
                .exchange().expectStatus().isForbidden();
        web.patch().uri("/api/orders/A-123").header("Origin", "https://boese.example").header("X-Jarvis", "1")
                .bodyValue(Map.of("version", 1)).exchange().expectStatus().isForbidden();
        web.post().uri("/api/approvals/abc123/freigeben").header("Origin", "http://localhost:8080").exchange()
                .expectStatus().isForbidden();
        assertEquals(List.of("GET /internal/orders"), calls);
    }

    @Test
    void konfliktWirdDurchgereichtUndEingabenGeprueft() {
        web.patch().uri("/api/orders/A-konflikt").header("Origin", "http://localhost:8080").header("X-Jarvis", "1")
                .bodyValue(Map.of("version", 1, "aenderungen", Map.of("status", "geplant"))).exchange()
                .expectStatus().isEqualTo(409).expectBody().jsonPath("$.aktuell.version").isEqualTo(7);
        web.post().uri("/api/approvals/abc123/loeschen").header("Origin", "http://localhost:8080").header("X-Jarvis", "1")
                .exchange().expectStatus().isBadRequest();
        web.get().uri("/api/calendar?von=2026-10-19&bis=;rm").exchange().expectStatus().isBadRequest();
        web.post().uri("/api/approvals/abc123/freigeben").header("Origin", "http://localhost:8080").header("X-Jarvis", "1")
                .exchange().expectStatus().isOk();
        assertTrue(calls.contains("POST /internal/approvals/abc123/freigeben"));
    }

    @Test
    void echtzeitUndEinstellungenLaufenUeber8080() {
        web.post().uri("/api/realtime/session").bodyValue(Map.of("pause", 1.0)).exchange().expectStatus().isForbidden();
        web.post().uri("/api/settings").header("Origin", "http://localhost:8080").bodyValue(Map.of("tempo", 1.1))
                .exchange().expectStatus().isForbidden();
        for (String path : List.of("/api/realtime/session", "/api/realtime/tool", "/api/jobs/extern", "/api/jobs/cancel",
                "/api/jobs/stop-all", "/api/relevanz", "/api/reset", "/api/tts/preview", "/api/settings"))
            web.post().uri(path).header("Origin", "http://localhost:8080").header("X-Jarvis", "1").bodyValue(Map.of())
                    .exchange().expectStatus().isOk();
        web.get().uri("/api/settings").exchange().expectStatus().isOk();
        web.get().uri("/api/logs/ereignisse").exchange().expectStatus().isOk();
        web.get().uri("/api/logs/passwoerter").exchange().expectStatus().isNotFound();
        web.get().uri("/api/events?after=5&timeout=99").exchange().expectStatus().isOk();
        assertTrue(calls.containsAll(List.of("POST /internal/eleven/session", "POST /internal/tool", "POST /internal/jobs/extern",
                "POST /internal/jobs/cancel", "POST /internal/jobs/stop-all", "POST /internal/relevanz", "POST /internal/reset",
                "POST /internal/tts/preview", "POST /internal/settings", "GET /internal/settings", "GET /internal/logs/ereignisse",
                "GET /internal/events?after=5&timeout=15.0")), calls.toString());
        assertFalse(calls.stream().anyMatch(c -> c.contains("passwoerter")));
    }

    @Test
    void faehigkeitenNurMitSchreibrechtSteuerbar() {
        web.get().uri("/api/faehigkeiten").exchange().expectStatus().isOk();
        web.post().uri("/api/faehigkeiten/abc123/abbrechen").header("Origin", "http://localhost:8080").exchange().expectStatus().isForbidden();
        web.post().uri("/api/faehigkeiten/abc123/loeschen").header("Origin", "http://localhost:8080").header("X-Jarvis", "1")
                .exchange().expectStatus().isBadRequest();
        web.post().uri("/api/faehigkeiten/abc123/abbrechen").header("Origin", "http://localhost:8080").header("X-Jarvis", "1")
                .exchange().expectStatus().isOk();
        assertTrue(calls.containsAll(List.of("GET /internal/faehigkeiten", "POST /internal/faehigkeiten/abc123/abbrechen")), calls.toString());
    }

    @Test
    void skillBibliothekSchreibtNurMitSchreibrecht() {
        web.post().uri("/api/faehigkeiten/starten").bodyValue(Map.of("name", "github_suchen", "args", Map.of())).exchange().expectStatus().isForbidden();
        web.post().uri("/api/skills/aktivieren").header("Origin", "http://localhost:8080").bodyValue(Map.of("id", "x:y", "aktiv", true))
                .exchange().expectStatus().isForbidden();
        web.post().uri("/api/faehigkeiten/starten").header("Origin", "http://localhost:8080").header("X-Jarvis", "1")
                .bodyValue(Map.of("name", "github_suchen", "args", Map.of("begriff", "x"))).exchange().expectStatus().isOk();
        web.post().uri("/api/skills/aktivieren").header("Origin", "http://localhost:8080").header("X-Jarvis", "1")
                .bodyValue(Map.of("id", "importiert:a/b", "aktiv", false)).exchange().expectStatus().isOk();
        assertTrue(calls.containsAll(List.of("POST /internal/faehigkeiten/starten", "POST /internal/skills/aktivieren")), calls.toString());
    }

    @Test
    void anhangNurMitSchreibrechtenUndMitGroessengrenze() {
        byte[] pdf = "%PDF-1.4 test".getBytes(java.nio.charset.StandardCharsets.UTF_8);
        web.post().uri("/api/anhaenge?raum=room-abc&name=a.pdf").bodyValue(pdf).exchange().expectStatus().isForbidden();
        web.post().uri("/api/anhaenge?raum=../x&name=a.pdf").header("Origin", "http://localhost:8080").header("X-Jarvis", "1")
                .bodyValue(pdf).exchange().expectStatus().isBadRequest();
        web.post().uri("/api/anhaenge?raum=room-abc&name=a.pdf").header("Origin", "http://localhost:8080").header("X-Jarvis", "1")
                .bodyValue(pdf).exchange().expectStatus().isOk().expectBody().jsonPath("$.anhang.id").isEqualTo("anh_x");
        byte[] big = new byte[ApiController.MAX_UPLOAD + 1];
        web.post().uri("/api/anhaenge?raum=room-abc&name=b.pdf").header("Origin", "http://localhost:8080").header("X-Jarvis", "1")
                .bodyValue(big).exchange().expectStatus().isEqualTo(413);
        web.delete().uri("/api/anhaenge/anh_0123456789abcdef01234567?raum=room-abc").header("Origin", "http://localhost:8080")
                .header("X-Jarvis", "1").exchange().expectStatus().isOk();
        web.delete().uri("/api/anhaenge/../../x?raum=room-abc").header("Origin", "http://localhost:8080")
                .header("X-Jarvis", "1").exchange().expectStatus().is4xxClientError();
        assertTrue(calls.contains("POST /internal/anhaenge?conversation_id=room-abc&name=a.pdf"));
        assertTrue(calls.stream().anyMatch(c -> c.startsWith("BYTES " + pdf.length + " application/octet-stream")));
        assertTrue(calls.contains("DELETE /internal/anhaenge/anh_0123456789abcdef01234567?conversation_id=room-abc"));
        assertFalse(calls.stream().anyMatch(c -> c.contains("b.pdf")));
    }
}
