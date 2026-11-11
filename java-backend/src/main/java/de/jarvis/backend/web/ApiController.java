package de.jarvis.backend.web;

import de.jarvis.backend.config.JarvisProperties;
import de.jarvis.backend.protocol.Protocol;
import de.jarvis.backend.security.SessionService;
import de.jarvis.backend.provider.PythonBridgeProvider;
import java.util.Map;
import org.springframework.http.HttpHeaders;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ServerWebExchange;
import reactor.core.publisher.Mono;
import org.springframework.beans.factory.annotation.Autowired;

@RestController
public final class ApiController {
    private final JarvisProperties props;
    private final SessionService sessions;
    private final PythonBridgeProvider bridge;
    public ApiController(JarvisProperties props, SessionService sessions) { this(props, sessions, null); }
    @Autowired
    public ApiController(JarvisProperties props, SessionService sessions, PythonBridgeProvider bridge) {
        this.props = props; this.sessions = sessions; this.bridge = bridge;
    }

    @GetMapping("/api/config")
    public Mono<ResponseEntity<?>> config(ServerWebExchange exchange) {
        String origin = exchange.getRequest().getHeaders().getOrigin();
        if (origin != null && !sessions.originAllowed(origin))
            return Mono.just(ResponseEntity.status(403).body(Map.of("message", "Origin nicht erlaubt.")));
        if (!sessions.authorized(exchange))
            return Mono.just(ResponseEntity.status(401).body(Map.of("auth_required", true)));
        return Mono.just(ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "no-store")
                .body(new Protocol.Config(props.isDemo(), props.isAuthRequired(), Protocol.defaultCities(), props.getCameras())));
    }

    public record Token(String token) {}

    @PostMapping("/api/session")
    public Mono<ResponseEntity<?>> session(@RequestBody Mono<Token> body, ServerWebExchange exchange) {
        if (!sessions.originAllowed(exchange.getRequest().getHeaders().getOrigin()))
            return Mono.just(ResponseEntity.status(403).body(Map.of("message", "Origin nicht erlaubt.")));
        return body.flatMap(value -> sessions.create(value.token())
                .<ResponseEntity<?>>map(cookie -> ResponseEntity.ok().header(HttpHeaders.SET_COOKIE, cookie.toString())
                        .header(HttpHeaders.CACHE_CONTROL, "no-store").body(Map.of("ok", true)))
                .defaultIfEmpty(ResponseEntity.status(401).body(Map.of("ok", false, "message", "Ungültiges Token."))));
    }

    @GetMapping("/health")
    public Map<String, Boolean> health() { return Map.of("ok", true); }

    @GetMapping("/api/memory")
    public Mono<ResponseEntity<?>> memory(@RequestParam(defaultValue = "") String q,
                                          @RequestParam(defaultValue = "12") int limit,
                                          ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("message", "Session erforderlich.")));
        if (bridge == null) return Mono.just(ResponseEntity.status(503).body(Map.of("message", "Python-Bridge nicht aktiv.")));
        return bridge.snapshot(Protocol.safe(q, 400), Math.min(50, Math.max(1, limit)))
                .map(value -> (ResponseEntity<?>) ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "no-store").body(value))
                .onErrorResume(error -> Mono.just((ResponseEntity) ResponseEntity.status(502).body(Map.of("message", "Memory-Bridge nicht erreichbar."))));
    }

    @GetMapping("/api/news")
    public Mono<ResponseEntity<?>> news(@RequestParam(defaultValue = "allgemein") String rubrik,
                                        @RequestParam(defaultValue = "") String stichwort,
                                        ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("message", "Session erforderlich.")));
        if (bridge == null) return Mono.just(ResponseEntity.status(503).body(Map.of("status", "fehler", "hinweis", "Python-Bridge nicht aktiv.")));
        if (!rubrik.matches("[a-z]{1,20}")) return Mono.just(ResponseEntity.badRequest().body(Map.of("message", "Ungültige Rubrik.")));
        return bridge.news(rubrik, Protocol.safe(stichwort, 80))
                .map(value -> (ResponseEntity<?>) ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "no-store").body(value))
                .onErrorResume(error -> Mono.just((ResponseEntity) ResponseEntity.status(502).body(Map.of("status", "fehler", "hinweis", "Nachrichten-Bridge nicht erreichbar."))));
    }

    @GetMapping("/api/market")
    public Mono<ResponseEntity<?>> market(@RequestParam(defaultValue = "") String q,
                                          @RequestParam(defaultValue = "false") boolean verzeichnis,
                                          ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("message", "Session erforderlich.")));
        if (bridge == null) return Mono.just(ResponseEntity.status(503).body(Map.of("status", "fehler", "hinweis", "Python-Bridge nicht aktiv.")));
        if (!verzeichnis && !q.matches("[\\p{L}0-9 .&-]{1,60}")) return Mono.just(ResponseEntity.badRequest().body(Map.of("message", "Ungültige Anfrage.")));
        Mono<Map<String, Object>> call = verzeichnis ? bridge.marketDirectory() : bridge.market(q);
        return call.map(value -> (ResponseEntity<?>) ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "no-store").body(value))
                .onErrorResume(error -> Mono.just((ResponseEntity) ResponseEntity.status(502).body(Map.of("status", "fehler", "hinweis", "Kurs-Bridge nicht erreichbar."))));
    }

    public record MemoryNote(String content, String category) { }
    @PostMapping("/api/memory")
    public Mono<ResponseEntity<?>> remember(@RequestBody Mono<MemoryNote> body, ServerWebExchange exchange) {
        if (!sessions.authorized(exchange) || !sessions.originAllowed(exchange.getRequest().getHeaders().getOrigin()))
            return Mono.just(ResponseEntity.status(403).body(Map.of("message", "Session und Origin erforderlich.")));
        if (bridge == null) return Mono.just(ResponseEntity.status(503).body(Map.of("message", "Python-Bridge nicht aktiv.")));
        return body.flatMap(note -> {
            if (note == null || note.content() == null || note.content().isBlank() || note.content().length() > 4000)
                return Mono.just((ResponseEntity<?>) ResponseEntity.badRequest().body(Map.of("message", "Notiz ist leer oder zu lang.")));
            String category = note.category() == null || note.category().isBlank() ? "note" : Protocol.safe(note.category(), 80);
            return bridge.remember(Protocol.safe(note.content(), 4000), category)
                    .map(value -> (ResponseEntity<?>) ResponseEntity.ok().body(value));
        }).onErrorResume(error -> Mono.just((ResponseEntity<?>) ResponseEntity.status(502).body(Map.of("message", "Memory-Bridge nicht erreichbar."))));
    }

    @DeleteMapping("/api/memory/{id}")
    public Mono<ResponseEntity<?>> forget(@PathVariable String id, ServerWebExchange exchange) {
        if (!sessions.authorized(exchange) || !sessions.originAllowed(exchange.getRequest().getHeaders().getOrigin()))
            return Mono.just(ResponseEntity.status(403).body(Map.of("message", "Session und Origin erforderlich.")));
        if (bridge == null || id == null || !id.matches("[A-Za-z0-9_-]{1,128}"))
            return Mono.just(ResponseEntity.badRequest().body(Map.of("message", "Ungültige Memory-ID.")));
        return bridge.forget(id).map(value -> (ResponseEntity<?>) ResponseEntity.ok().body(value))
                .onErrorResume(error -> Mono.just((ResponseEntity) ResponseEntity.status(502).body(Map.of("message", "Memory-Bridge nicht erreichbar."))));
    }

    // --- Kommandozentrale: Aufträge, Kalender, Freigaben, Jobs (Proxy zur lokalen Python-Brücke) ---

    private static final java.util.regex.Pattern ID = java.util.regex.Pattern.compile("[A-Za-z0-9_-]{1,64}");
    private static final java.util.regex.Pattern DATE = java.util.regex.Pattern.compile("[0-9T:.-]{0,25}");

    private boolean writeAllowed(ServerWebExchange exchange) {
        var headers = exchange.getRequest().getHeaders();
        return sessions.authorized(exchange) && sessions.originAllowed(headers.getOrigin())
                && "1".equals(headers.getFirst("X-Jarvis"));
    }

    private Mono<ResponseEntity<?>> forward(String method, String path, Object body) {
        if (bridge == null) return Mono.just(ResponseEntity.status(503).body(Map.of("fehler", "Python-Bridge nicht aktiv.")));
        return bridge.command(method, path, body)
                .<ResponseEntity<?>>map(value -> ResponseEntity.status(value.getStatusCode())
                        .header(HttpHeaders.CACHE_CONTROL, "no-store").body(value.getBody()))
                .onErrorResume(error -> Mono.just(ResponseEntity.status(502)
                        .body(Map.of("fehler", "Bridge nicht erreichbar."))));
    }

    @GetMapping("/api/orders")
    public Mono<ResponseEntity<?>> orders(ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        return forward("GET", "/internal/orders", null);
    }

    @PostMapping("/api/orders")
    public Mono<ResponseEntity<?>> createOrder(@RequestBody Mono<Map<String, Object>> body, ServerWebExchange exchange) {
        if (!writeAllowed(exchange)) return Mono.just(ResponseEntity.status(403).body(Map.of("fehler", "Session, Origin und X-Jarvis erforderlich.")));
        return body.flatMap(value -> forward("POST", "/internal/orders", value));
    }

    @PatchMapping("/api/orders/{id}")
    public Mono<ResponseEntity<?>> updateOrder(@PathVariable String id, @RequestBody Mono<Map<String, Object>> body,
                                               ServerWebExchange exchange) {
        if (!writeAllowed(exchange)) return Mono.just(ResponseEntity.status(403).body(Map.of("fehler", "Session, Origin und X-Jarvis erforderlich.")));
        if (!ID.matcher(id).matches()) return Mono.just(ResponseEntity.badRequest().body(Map.of("fehler", "Ungültige ID.")));
        return body.flatMap(value -> forward("PATCH", "/internal/orders/" + id, value));
    }

    @GetMapping("/api/calendar")
    public Mono<ResponseEntity<?>> calendar(@RequestParam(defaultValue = "") String von, @RequestParam(defaultValue = "") String bis,
                                            ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        if (!DATE.matcher(von).matches() || !DATE.matcher(bis).matches())
            return Mono.just(ResponseEntity.badRequest().body(Map.of("fehler", "Ungültiger Zeitraum.")));
        return forward("GET", "/internal/calendar?von=" + PythonBridgeProvider.query(von) + "&bis=" + PythonBridgeProvider.query(bis), null);
    }

    @GetMapping("/api/approvals")
    public Mono<ResponseEntity<?>> approvals(ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        return forward("GET", "/internal/approvals", null);
    }

    @PostMapping("/api/approvals/{id}/{aktion}")
    public Mono<ResponseEntity<?>> decide(@PathVariable String id, @PathVariable String aktion, ServerWebExchange exchange) {
        if (!writeAllowed(exchange)) return Mono.just(ResponseEntity.status(403).body(Map.of("fehler", "Session, Origin und X-Jarvis erforderlich.")));
        if (!ID.matcher(id).matches() || !(aktion.equals("freigeben") || aktion.equals("verwerfen")))
            return Mono.just(ResponseEntity.badRequest().body(Map.of("fehler", "Ungültige Freigabe.")));
        return forward("POST", "/internal/approvals/" + id + "/" + aktion, Map.of());
    }

    @GetMapping("/api/geocode")
    public Mono<ResponseEntity<?>> geocode(@RequestParam(defaultValue = "") String q, ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        if (q.isBlank() || q.length() > 120) return Mono.just(ResponseEntity.badRequest().body(Map.of("fehler", "Ungültige Suche.")));
        return forward("GET", "/internal/geocode?q=" + PythonBridgeProvider.query(q), null);
    }

    @GetMapping("/api/jobs")
    public Mono<ResponseEntity<?>> jobs(ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        return forward("GET", "/internal/jobs", null);
    }

    /** Echter Status von Agenten, Abläufen, n8n und Integrationen (nur lesend, ohne Zugangsdaten). */
    @GetMapping("/api/automations")
    public Mono<ResponseEntity<?>> automations(ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        return forward("GET", "/internal/automations", null);
    }

    // --- Aus der früheren Sprachoberfläche (8765) übernommen: Einstellungen, Echtzeit-Modus, Jobs, Protokolle ---

    private static final java.util.Set<String> LOGS = java.util.Set.of("audit", "aktionen", "ereignisse");
    private static final Map<String, String> WRITES = Map.of(
            "realtime/session", "/internal/eleven/session", "realtime/tool", "/internal/tool",
            "jobs/extern", "/internal/jobs/extern", "jobs/cancel", "/internal/jobs/cancel", "jobs/stop-all", "/internal/jobs/stop-all",
            "relevanz", "/internal/relevanz", "reset", "/internal/reset", "tts/preview", "/internal/tts/preview",
            "settings", "/internal/settings");

    private Mono<ResponseEntity<?>> write(String key, Mono<Map<String, Object>> body, ServerWebExchange exchange) {
        if (!writeAllowed(exchange)) return Mono.just(ResponseEntity.status(403).body(Map.of("fehler", "Session, Origin und X-Jarvis erforderlich.")));
        return body.defaultIfEmpty(Map.of()).flatMap(value -> forward("POST", WRITES.get(key), value));
    }

    @GetMapping({"/api/settings", "/api/status"})
    public Mono<ResponseEntity<?>> settings(ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        return forward("GET", "/internal/settings", null);
    }

    @PostMapping("/api/settings")
    public Mono<ResponseEntity<?>> saveSettings(@RequestBody(required = false) Mono<Map<String, Object>> body, ServerWebExchange exchange) {
        return write("settings", body, exchange);
    }

    @PostMapping({"/api/realtime/session", "/api/realtime/tool", "/api/jobs/extern", "/api/jobs/cancel", "/api/jobs/stop-all",
            "/api/relevanz", "/api/reset", "/api/tts/preview"})
    public Mono<ResponseEntity<?>> realtime(@RequestBody(required = false) Mono<Map<String, Object>> body, ServerWebExchange exchange) {
        String key = exchange.getRequest().getPath().value().substring("/api/".length());
        return write(key, body == null ? Mono.empty() : body, exchange);
    }

    // --- Dateianhänge im Chat: roher Body mit fester Obergrenze, nur im Arbeitsspeicher, Prüfung in Python ---

    /** Obergrenze je Datei (muss zu jarvis/anhaenge.py MAX_FILE passen). */
    static final int MAX_UPLOAD = 10 * 1024 * 1024;

    @PostMapping("/api/anhaenge")
    public Mono<ResponseEntity<?>> uploadAttachment(@RequestParam(defaultValue = "") String raum, @RequestParam(defaultValue = "") String name,
                                                    ServerWebExchange exchange) {
        if (!writeAllowed(exchange)) return Mono.just(ResponseEntity.status(403).body(Map.of("fehler", "Session, Origin und X-Jarvis erforderlich.")));
        if (!ID.matcher(raum).matches() || name.isBlank() || name.length() > 200)
            return Mono.just(ResponseEntity.badRequest().body(Map.of("fehler", "Ungültiger Raum oder Dateiname.")));
        long declared = exchange.getRequest().getHeaders().getContentLength();
        if (declared > MAX_UPLOAD) return Mono.just(tooLarge());
        if (bridge == null) return Mono.just(ResponseEntity.status(503).body(Map.of("fehler", "Python-Bridge nicht aktiv.")));
        String path = "/internal/anhaenge?conversation_id=" + PythonBridgeProvider.query(raum) + "&name=" + PythonBridgeProvider.query(name);
        return org.springframework.core.io.buffer.DataBufferUtils.join(exchange.getRequest().getBody(), MAX_UPLOAD)
                .map(buffer -> {
                    byte[] bytes = new byte[buffer.readableByteCount()];
                    buffer.read(bytes);
                    org.springframework.core.io.buffer.DataBufferUtils.release(buffer);
                    return bytes;
                })
                .defaultIfEmpty(new byte[0])
                .flatMap(bytes -> bytes.length == 0
                        ? Mono.just((ResponseEntity<?>) ResponseEntity.badRequest().body(Map.of("fehler", "Die Datei ist leer.")))
                        : bridge.upload(path, bytes).<ResponseEntity<?>>map(value -> ResponseEntity.status(value.getStatusCode())
                                .header(HttpHeaders.CACHE_CONTROL, "no-store").body(value.getBody())))
                .onErrorResume(org.springframework.core.io.buffer.DataBufferLimitException.class, error -> Mono.just(tooLarge()))
                .onErrorResume(error -> Mono.just(ResponseEntity.status(502).body(Map.of("fehler", "Bridge nicht erreichbar."))));
    }

    private static ResponseEntity<?> tooLarge() {
        return ResponseEntity.status(413).body(Map.of("fehler", "Die Datei ist zu groß (höchstens " + (MAX_UPLOAD / (1024 * 1024)) + " MB)."));
    }

    @GetMapping("/api/anhaenge")
    public Mono<ResponseEntity<?>> attachments(@RequestParam(defaultValue = "") String raum, ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        if (!ID.matcher(raum).matches()) return Mono.just(ResponseEntity.badRequest().body(Map.of("fehler", "Ungültiger Raum.")));
        return forward("GET", "/internal/anhaenge?conversation_id=" + PythonBridgeProvider.query(raum), null);
    }

    @DeleteMapping("/api/anhaenge/{id}")
    public Mono<ResponseEntity<?>> removeAttachment(@PathVariable String id, @RequestParam(defaultValue = "") String raum, ServerWebExchange exchange) {
        if (!writeAllowed(exchange)) return Mono.just(ResponseEntity.status(403).body(Map.of("fehler", "Session, Origin und X-Jarvis erforderlich.")));
        if (!ID.matcher(raum).matches() || !id.matches("anh_[a-f0-9]{24}"))
            return Mono.just(ResponseEntity.badRequest().body(Map.of("fehler", "Ungültiger Anhang.")));
        return forward("DELETE", "/internal/anhaenge/" + id + "?conversation_id=" + PythonBridgeProvider.query(raum), null);
    }

    /** Anruf-Copilot: Projekte, Kennzahlen, letzte Anrufe (nur lesend). */
    @GetMapping("/api/anrufe")
    public Mono<ResponseEntity<?>> anrufe(@RequestParam(defaultValue = "7") int tage, ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        return forward("GET", "/internal/anrufe?tage=" + Math.max(1, Math.min(365, tage)), null);
    }

    /** Content-Workflow: Einträge lesen; Status ab „Freigegeben“ nur mit Session, Origin und X-Jarvis. Keine Veröffentlichung. */
    @GetMapping("/api/content")
    public Mono<ResponseEntity<?>> content(ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        return forward("GET", "/internal/content", null);
    }

    @PostMapping("/api/content/status")
    public Mono<ResponseEntity<?>> contentStatus(@RequestBody Mono<Map<String, Object>> body, ServerWebExchange exchange) {
        if (!writeAllowed(exchange)) return Mono.just(ResponseEntity.status(403).body(Map.of("fehler", "Session, Origin und X-Jarvis erforderlich.")));
        return body.flatMap(value -> forward("POST", "/internal/content/status", value));
    }

    /** Jarvis-Laufzeitfähigkeiten: Definitionen und letzte Läufe (Status, Ergebnis, Abbruch). */
    @GetMapping("/api/faehigkeiten")
    public Mono<ResponseEntity<?>> faehigkeiten(ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        return forward("GET", "/internal/faehigkeiten", null);
    }

    @PostMapping("/api/faehigkeiten/starten")
    public Mono<ResponseEntity<?>> faehigkeitStarten(@RequestBody Mono<Map<String, Object>> body, ServerWebExchange exchange) {
        if (!writeAllowed(exchange)) return Mono.just(ResponseEntity.status(403).body(Map.of("fehler", "Session, Origin und X-Jarvis erforderlich.")));
        return body.flatMap(value -> forward("POST", "/internal/faehigkeiten/starten", value));
    }

    @PostMapping("/api/skills/aktivieren")
    public Mono<ResponseEntity<?>> skillAktivieren(@RequestBody Mono<Map<String, Object>> body, ServerWebExchange exchange) {
        if (!writeAllowed(exchange)) return Mono.just(ResponseEntity.status(403).body(Map.of("fehler", "Session, Origin und X-Jarvis erforderlich.")));
        return body.flatMap(value -> forward("POST", "/internal/skills/aktivieren", value));
    }

    @PostMapping("/api/faehigkeiten/{id}/{aktion}")
    public Mono<ResponseEntity<?>> faehigkeitAktion(@PathVariable String id, @PathVariable String aktion, ServerWebExchange exchange) {
        if (!writeAllowed(exchange)) return Mono.just(ResponseEntity.status(403).body(Map.of("fehler", "Session, Origin und X-Jarvis erforderlich.")));
        if (!ID.matcher(id).matches() || !(aktion.equals("abbrechen") || aktion.equals("wiederholen")))
            return Mono.just(ResponseEntity.badRequest().body(Map.of("fehler", "Ungültige Aktion.")));
        return forward("POST", "/internal/faehigkeiten/" + id + "/" + aktion, Map.of());
    }

    // --- Agenten-Center, Verbindungen, Gedächtnis-Center, fortlaufendes Transkript ---

    private static final java.util.Set<String> MEMORY_ACTIONS =
            java.util.Set.of("speichern", "aendern", "bestaetigen", "loeschen", "archiv_loeschen", "index_neu");

    /** Echte Jobs je Agent mit Schritten, Zeiten, Ergebnis und Rollenabdeckung (nur lesend). */
    @GetMapping("/api/agenten")
    public Mono<ResponseEntity<?>> agenten(ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        return forward("GET", "/internal/agenten", null);
    }

    /** Tatsächlicher Zustand der Dienste (live geprüfte Rechte, keine Zugangsdaten). */
    @GetMapping("/api/verbindungen")
    public Mono<ResponseEntity<?>> verbindungen(@RequestParam(defaultValue = "false") boolean neu, ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        return forward("GET", "/internal/verbindungen" + (neu ? "?neu=1" : ""), null);
    }

    @GetMapping("/api/gedaechtnis")
    public Mono<ResponseEntity<?>> gedaechtnis(@RequestParam(defaultValue = "") String q, @RequestParam(defaultValue = "60") int limit,
                                               ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        return forward("GET", "/internal/gedaechtnis?q=" + PythonBridgeProvider.query(Protocol.safe(q, 400))
                + "&limit=" + Math.max(1, Math.min(200, limit)), null);
    }

    @GetMapping("/api/gedaechtnis/export")
    public Mono<ResponseEntity<?>> gedaechtnisExport(ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        return forward("GET", "/internal/gedaechtnis/export", null);
    }

    @GetMapping("/api/gedaechtnis/verlauf/{id}")
    public Mono<ResponseEntity<?>> gedaechtnisVerlauf(@PathVariable String id, ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        if (!ID.matcher(id).matches()) return Mono.just(ResponseEntity.badRequest().body(Map.of("fehler", "Ungültige ID.")));
        return forward("GET", "/internal/gedaechtnis/verlauf/" + id, null);
    }

    /** Korrigieren, bestätigen, löschen: nur der Nutzer im Dashboard (Session, Origin, X-Jarvis). */
    @PostMapping("/api/gedaechtnis/{aktion}")
    public Mono<ResponseEntity<?>> gedaechtnisAktion(@PathVariable String aktion, @RequestBody(required = false) Mono<Map<String, Object>> body,
                                                     ServerWebExchange exchange) {
        if (!writeAllowed(exchange)) return Mono.just(ResponseEntity.status(403).body(Map.of("fehler", "Session, Origin und X-Jarvis erforderlich.")));
        if (!MEMORY_ACTIONS.contains(aktion)) return Mono.just(ResponseEntity.badRequest().body(Map.of("fehler", "Ungültige Aktion.")));
        return (body == null ? Mono.<Map<String, Object>>empty() : body).defaultIfEmpty(Map.of())
                .flatMap(value -> forward("POST", "/internal/gedaechtnis/" + aktion, value));
    }

    /** Erkannte Äußerung aus dem Echtzeit-Sprachmodus ins Gesprächsarchiv (nur Text). */
    @PostMapping("/api/transkript")
    public Mono<ResponseEntity<?>> transkript(@RequestBody Mono<Map<String, Object>> body, ServerWebExchange exchange) {
        if (!writeAllowed(exchange)) return Mono.just(ResponseEntity.status(403).body(Map.of("fehler", "Session, Origin und X-Jarvis erforderlich.")));
        return body.flatMap(value -> forward("POST", "/internal/transkript", value));
    }

    @GetMapping("/api/logs/{art}")
    public Mono<ResponseEntity<?>> logs(@PathVariable String art, ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        if (!LOGS.contains(art)) return Mono.just(ResponseEntity.status(404).body(Map.of("fehler", "Unbekanntes Protokoll.")));
        return forward("GET", "/internal/logs/" + art, null);
    }

    /** Long-Polling auf den Ereignis-Bus (Kameras, Jobs); höchstens 15 s je Anfrage. */
    @GetMapping("/api/events")
    public Mono<ResponseEntity<?>> events(@RequestParam(defaultValue = "0") long after, @RequestParam(defaultValue = "0") double timeout,
                                          ServerWebExchange exchange) {
        if (!sessions.authorized(exchange)) return Mono.just(ResponseEntity.status(401).body(Map.of("fehler", "Session erforderlich.")));
        long safeAfter = Math.max(0, after);
        double safeTimeout = Double.isFinite(timeout) ? Math.max(0, Math.min(15, timeout)) : 0;
        return forward("GET", "/internal/events?after=" + safeAfter + "&timeout=" + safeTimeout, null);
    }

    @GetMapping(value = "/", produces = "text/html")
    public ResponseEntity<org.springframework.core.io.Resource> index() {
        var resource = new org.springframework.core.io.FileSystemResource(
                java.nio.file.Path.of(props.getStaticDir()).resolve("index.html"));
        if (!resource.isReadable()) return ResponseEntity.status(503).build();
        return ResponseEntity.ok().header(HttpHeaders.CACHE_CONTROL, "no-cache").body(resource);
    }
}
