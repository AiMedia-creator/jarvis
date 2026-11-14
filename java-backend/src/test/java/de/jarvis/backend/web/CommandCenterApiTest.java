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
    private de.jarvis.backend.room.RoomRegistry rooms;

    @BeforeEach
    void setUp() {
        bridgeServer = HttpServer.create().host("127.0.0.1").port(0).handle((request, response) -> {
            calls.add(request.method().name() + " " + request.uri());
            if ((request.uri().startsWith("/internal/anhaenge") || request.uri().startsWith("/internal/leads/import")) && request.method().name().equals("POST"))
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
        rooms = new de.jarvis.backend.room.RoomRegistry(props, null, null, new ObjectMapper());
        web = WebTestClient.bindToController(new ApiController(props, new SessionService(props), bridge, rooms)).build();
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
    void leadsLesenMitSessionUndSeitenparametern() {
        web.get().uri("/api/leads?status=offen&q=Muster GmbH").exchange().expectStatus().isOk();
        web.get().uri("/api/leads?offset=150&limit=50&snapshot=S-abc_123&um=L-abc123").exchange().expectStatus().isOk();
        web.get().uri("/api/leads?offset=-5&limit=99999").exchange().expectStatus().isOk();
        web.get().uri("/api/leads/session?seit=1794430000.5").exchange().expectStatus().isOk();
        web.get().uri("/api/leads/L-abc123").exchange().expectStatus().isOk();
        web.get().uri("/api/leads?status=;drop").exchange().expectStatus().isBadRequest();
        web.get().uri("/api/leads?snapshot=../x").exchange().expectStatus().isBadRequest();
        web.get().uri("/api/leads?um=a b").exchange().expectStatus().isBadRequest();
        assertTrue(calls.containsAll(List.of("GET /internal/leads/session?seit=1794430000", "GET /internal/leads/L-abc123",
                "GET /internal/leads?status=&q=&offset=150&limit=50&snapshot=S-abc_123&um=L-abc123",
                "GET /internal/leads?status=&q=&offset=0&limit=200&snapshot=&um=")), calls.toString());
        assertTrue(calls.stream().anyMatch(c -> c.startsWith("GET /internal/leads?status=offen&q=Muster")), calls.toString());
    }

    /** Alle schreibenden Lead-Aufrufe: (Methode, Pfad). */
    private static final List<String[]> LEAD_WRITES = List.of(
            new String[] {"POST", "/api/leads"}, new String[] {"POST", "/api/leads/import?ausfuehren=true"},
            new String[] {"PATCH", "/api/leads/L-abc123"}, new String[] {"POST", "/api/leads/L-abc123/ergebnis"},
            new String[] {"POST", "/api/leads/L-abc123/notiz"});

    private WebTestClient.RequestHeadersSpec<?> leadWrite(String[] call, String origin, String header, String room, String ticket) {
        WebTestClient.RequestBodySpec spec = web.method(org.springframework.http.HttpMethod.valueOf(call[0])).uri(call[1]);
        if (origin != null) spec.header("Origin", origin);
        if (header != null) spec.header("X-Jarvis", header);
        if (room != null) spec.header("X-Jarvis-Room", room);
        if (ticket != null) spec.header("X-Jarvis-Owner", ticket);
        return call[1].contains("/import")
                ? spec.contentType(org.springframework.http.MediaType.valueOf("text/csv")).bodyValue("firma,name\nA AG,Anna\n".getBytes(java.nio.charset.StandardCharsets.UTF_8))
                : spec.bodyValue(Map.of("version", 1, "felder", Map.of("firma", "A"), "aenderungen", Map.of("rolle", "GF")));
    }

    @Test
    void leadsSchreibenNurDerBesitzerDesRaums() {
        de.jarvis.backend.room.Room room = rooms.getOrCreate("room-crm");
        room.join("besitzer", "main", "lokal");
        room.join("beobachter", "main", "lokal");
        String ticket = room.ownerTicketFor("besitzer");
        assertNotNull(ticket);
        assertNull(room.ownerTicketFor("beobachter"), "Beobachter bekommen kein Ticket");
        String ok = "http://localhost:8080";
        for (String[] call : LEAD_WRITES) {
            // Beobachter: gültige Session, erlaubter Origin und X-Jarvis reichen NICHT – der statische Header ist kein Nachweis.
            leadWrite(call, ok, "1", null, null).exchange().expectStatus().isForbidden();
            leadWrite(call, ok, "1", "room-crm", null).exchange().expectStatus().isForbidden();
            leadWrite(call, ok, "1", "room-crm", "geraten").exchange().expectStatus().isForbidden();
            leadWrite(call, ok, "1", "room-crm", "beobachter").exchange().expectStatus().isForbidden();
            leadWrite(call, ok, "1", "room-crm", ticket + "x").exchange().expectStatus().isForbidden();
            // Richtiges Ticket, aber falscher/unbekannter Raum
            leadWrite(call, ok, "1", "room-anders", ticket).exchange().expectStatus().isForbidden();
            leadWrite(call, ok, "1", "../room-crm", ticket).exchange().expectStatus().isForbidden();
            // Richtiges Ticket, aber Origin oder X-Jarvis fehlen oder sind falsch
            leadWrite(call, null, "1", "room-crm", ticket).exchange().expectStatus().isForbidden();
            leadWrite(call, "https://boese.example", "1", "room-crm", ticket).exchange().expectStatus().isForbidden();
            leadWrite(call, ok, null, "room-crm", ticket).exchange().expectStatus().isForbidden();
            leadWrite(call, ok, "0", "room-crm", ticket).exchange().expectStatus().isForbidden();
        }
        assertEquals(List.of(), calls, "kein abgelehnter Aufruf darf die Brücke erreichen");
        assertEquals(1, rooms.size(), "die Prüfung legt keine Räume an");
        for (String[] call : LEAD_WRITES) leadWrite(call, ok, "1", "room-crm", ticket).exchange().expectStatus().isOk();
        assertTrue(calls.containsAll(List.of("POST /internal/leads", "POST /internal/leads/import?ausfuehren=1",
                "PATCH /internal/leads/L-abc123", "POST /internal/leads/L-abc123/ergebnis", "POST /internal/leads/L-abc123/notiz")), calls.toString());
        web.post().uri("/api/leads/L-abc123/loeschen").header("Origin", ok).header("X-Jarvis", "1").header("X-Jarvis-Room", "room-crm")
                .header("X-Jarvis-Owner", ticket).bodyValue(Map.of()).exchange().expectStatus().isBadRequest();
        // Verlässt der Besitzer den Raum, erlischt sein Ticket; ein neuer Besitzer bekommt ein anderes.
        calls.clear();
        room.leave("besitzer");
        for (String[] call : LEAD_WRITES) leadWrite(call, ok, "1", "room-crm", ticket).exchange().expectStatus().isForbidden();
        room.join("neuer-besitzer", "main", "lokal");
        String neu = room.ownerTicketFor("neuer-besitzer");
        assertNotEquals(ticket, neu);
        leadWrite(LEAD_WRITES.get(0), ok, "1", "room-crm", ticket).exchange().expectStatus().isForbidden();
        leadWrite(LEAD_WRITES.get(0), ok, "1", "room-crm", neu).exchange().expectStatus().isOk();
        assertEquals(List.of("POST /internal/leads"), calls);
    }

    @Test
    void leadsSchreibenBrauchtGueltigeSessionDesBesitzers() {
        JarvisProperties props = new JarvisProperties();
        props.setBridgeUrl("http://127.0.0.1:" + bridgeServer.port());
        props.setAuthRequired(true);
        props.setSessionToken("unit-test-session-token");
        props.setBridgeToken("unit-test-bridge-key");
        SessionService sessions = new SessionService(props);
        var registry = new de.jarvis.backend.room.RoomRegistry(props, null, null, new ObjectMapper());
        WebTestClient secured = WebTestClient.bindToController(new ApiController(props, sessions,
                new PythonBridgeProvider(WebClient.builder(), props, new ObjectMapper()), registry)).build();
        String besitzerCookie = "JARVIS_SESSION=" + sessions.create("unit-test-session-token").block().getValue();
        String andererCookie = "JARVIS_SESSION=" + sessions.create("unit-test-session-token").block().getValue();
        de.jarvis.backend.room.Room room = registry.getOrCreate("room-crm");
        room.join("besitzer", "main", sessions.sessionKey(besitzerCookie));
        String ticket = room.ownerTicketFor("besitzer");
        String ok = "http://localhost:8080";
        java.util.function.BiFunction<String, String, WebTestClient.ResponseSpec> post = (cookie, owner) -> {
            WebTestClient.RequestBodySpec spec = secured.post().uri("/api/leads").header("Origin", ok).header("X-Jarvis", "1")
                    .header("X-Jarvis-Room", "room-crm").header("X-Jarvis-Owner", owner);
            if (cookie != null) spec.header("Cookie", cookie);
            return spec.bodyValue(Map.of("felder", Map.of("firma", "A"))).exchange();
        };
        post.apply(null, ticket).expectStatus().isForbidden();                              // keine Session
        post.apply("JARVIS_SESSION=erfunden", ticket).expectStatus().isForbidden();          // ungültige Session
        post.apply(andererCookie, ticket).expectStatus().isForbidden();                      // gültige, aber fremde Session
        post.apply(besitzerCookie, "falsch").expectStatus().isForbidden();                   // richtige Session, falsches Ticket
        secured.get().uri("/api/leads").exchange().expectStatus().isUnauthorized();          // Lesen ohne Session
        secured.get().uri("/api/leads").header("Cookie", andererCookie).exchange().expectStatus().isOk();  // Beobachter darf lesen
        assertEquals(List.of("GET /internal/leads?status=&q=&offset=0&limit=50&snapshot=&um="), calls);
        post.apply(besitzerCookie, ticket).expectStatus().isOk();
        assertTrue(calls.contains("POST /internal/leads"));
        assertEquals("", sessions.sessionKey("JARVIS_SESSION=erfunden"));
        assertNotEquals(sessions.sessionKey(besitzerCookie), sessions.sessionKey(andererCookie));
    }

    @Test
    void besitzerTicketNurImWelcomeDesBesitzers() throws Exception {
        de.jarvis.backend.room.Room room = rooms.getOrCreate("room-welcome");
        var besitzer = room.join("client-besitzer", "main", "lokal");
        var beobachter = room.join("client-beobachter", "main", "lokal");
        var ops = room.join("client-ops1", "ops", "lokal");
        ObjectMapper mapper = new ObjectMapper();
        Map<?, ?> first = mapper.readValue(room.controlFlux(besitzer).blockFirst(java.time.Duration.ofSeconds(2)), Map.class);
        assertEquals("owner", first.get("role"));
        assertEquals(room.ownerTicketFor("client-besitzer"), first.get("owner_ticket"));
        for (var other : List.of(beobachter, ops)) {
            Map<?, ?> welcome = mapper.readValue(room.controlFlux(other).blockFirst(java.time.Duration.ofSeconds(2)), Map.class);
            assertEquals("observer", welcome.get("role"));
            assertFalse(welcome.containsKey("owner_ticket"));
        }
        assertFalse(room.ownerTicketValid(null, "lokal"));
        assertFalse(room.ownerTicketValid(room.ownerTicketFor("client-besitzer"), ""));
        assertTrue(room.ownerTicketValid(room.ownerTicketFor("client-besitzer"), "lokal"));
    }

    @Test
    void csvImportBisEinMegabyteAlsRohdaten() {
        de.jarvis.backend.room.Room room = rooms.getOrCreate("room-csv");
        room.join("besitzer", "main", "lokal");
        String ticket = room.ownerTicketFor("besitzer");
        java.util.function.Function<byte[], WebTestClient.ResponseSpec> send = bytes -> web.post().uri("/api/leads/import")
                .header("Origin", "http://localhost:8080").header("X-Jarvis", "1").header("X-Jarvis-Room", "room-csv")
                .header("X-Jarvis-Owner", ticket).contentType(org.springframework.http.MediaType.valueOf("text/csv")).bodyValue(bytes).exchange();
        byte[] genau = new byte[ApiController.MAX_CSV];
        java.util.Arrays.fill(genau, (byte) 'a');
        send.apply(genau).expectStatus().isOk();                                             // genau 1 MB (weit über den alten 200 KB)
        send.apply(java.util.Arrays.copyOf(genau, ApiController.MAX_CSV - 1)).expectStatus().isOk();
        byte[] zuGross = java.util.Arrays.copyOf(genau, ApiController.MAX_CSV + 1);
        send.apply(zuGross).expectStatus().isEqualTo(413).expectBody().jsonPath("$.fehler").isEqualTo("Die CSV-Datei ist zu groß (höchstens 1 MB).");
        send.apply(new byte[0]).expectStatus().isBadRequest();
        assertEquals(List.of("POST /internal/leads/import?ausfuehren=0", "BYTES 1000000 application/octet-stream",
                "POST /internal/leads/import?ausfuehren=0", "BYTES 999999 application/octet-stream"), calls);
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

    @Test
    void alleErinnerungenLoeschenNurMitSchreibrechten() {
        web.post().uri("/api/gedaechtnis/alle_loeschen").bodyValue(Map.of("bestaetigt", true)).exchange().expectStatus().isForbidden();
        web.post().uri("/api/gedaechtnis/alle_loeschen").header("Origin", "http://localhost:8080").header("X-Jarvis", "1")
                .bodyValue(Map.of("bestaetigt", true)).exchange().expectStatus().isOk();
        web.post().uri("/api/gedaechtnis/alles_weg").header("Origin", "http://localhost:8080").header("X-Jarvis", "1")
                .bodyValue(Map.of()).exchange().expectStatus().isBadRequest();
        assertEquals(List.of("POST /internal/gedaechtnis/alle_loeschen"), calls);
    }
}
