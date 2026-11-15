package de.jarvis.backend.room;

import com.fasterxml.jackson.databind.ObjectMapper;
import de.jarvis.backend.audio.AudioFrame;
import de.jarvis.backend.audio.AudioRingBuffer;
import de.jarvis.backend.audio.CancellationToken;
import de.jarvis.backend.config.JarvisProperties;
import de.jarvis.backend.provider.GenerationContext;
import de.jarvis.backend.provider.GenerationProvider;
import de.jarvis.backend.provider.SentenceSplitter;
import de.jarvis.backend.provider.SttProvider;
import de.jarvis.backend.provider.TtsProvider;
import de.jarvis.backend.protocol.Protocol;
import java.lang.management.ManagementFactory;
import java.lang.management.ThreadMXBean;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.atomic.AtomicLong;
import java.util.concurrent.atomic.AtomicReference;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import reactor.core.Disposable;
import reactor.core.publisher.Flux;
import reactor.core.publisher.Mono;
import reactor.core.publisher.Sinks;
import reactor.core.scheduler.Schedulers;

/** A single bounded, owner-controlled conversation room. */
public final class Room {
    /** Wie viele Sätze parallel vertont werden dürfen (Reihenfolge bleibt erhalten). */
    static final int TTS_PREFETCH = 2;

    private static final Logger log = LoggerFactory.getLogger(Room.class);
    private static final int HISTORY_LIMIT = 100;
    private static final int CONTROL_QUEUE_LIMIT = 2048;
    /** Zwischenstände des Antworttexts höchstens so oft senden (sonst läuft bei langen Antworten der Puffer über). */
    static final long TRANSCRIPT_INTERVAL_NANOS = 150_000_000L;
    private static final long MAX_AUDIO_BYTES = 1_920_000L;  // 60 s

    public enum Screen { MAIN, OPS }

    public record Client(String id, Screen screen, Sinks.Many<String> control) { }

    private final String id;
    private final int maxClients;
    private final int maxInputChars;
    private final boolean demo;
    private final GenerationProvider generation;
    private final TtsProvider tts;
    private final SttProvider stt;
    private final ObjectMapper mapper;
    private final AudioRingBuffer ring;
    private final List<Protocol.City> cities;
    private final List<Protocol.Camera> cameras;
    private final long startedAt = System.nanoTime();

    private final ConcurrentHashMap<String, Client> clients = new ConcurrentHashMap<>();
    /** This sink deliberately survives STOP_STREAM: a late audio websocket sees the next turn. */
    private final AtomicReference<Sinks.Many<AudioFrame>> audioSink;
    private final AtomicReference<java.util.concurrent.ArrayBlockingQueue<AudioFrame>> audioQueue = new AtomicReference<>();
    private final AtomicLong interrupts = new AtomicLong();
    private final AtomicLong droppedMessages = new AtomicLong();
    private final Map<String, Map<String, Object>> transcriptHistory = new LinkedHashMap<>();
    private final List<Map<String, Object>> logHistory = new ArrayList<>();
    private final ThreadMXBean threadMx = ManagementFactory.getThreadMXBean();

    private long epoch;
    private long nextSequence;
    private String owner;
    /** Zufälliges Ticket des aktuellen Besitzers; nur er bekommt es (WELCOME). Erlischt, sobald er den Raum verlässt. */
    private String ownerTicket;
    /** Session, über die der Besitzer verbunden ist (Schlüssel aus SessionService, nie die Session-ID). */
    private String ownerSession;
    private static final java.security.SecureRandom TICKETS = new java.security.SecureRandom();
    private String audioOwner;
    private UUID streamId;
    private CancellationToken generationToken;
    private Disposable generationSubscription;
    /** Ein gesprochener Abschnitt mit eigener Spracherkennung; Abschnitte werden in Reihenfolge zusammengefügt. */
    private static final class Fragment {
        final UUID id; final CancellationToken token; final long startedNanos;
        Disposable subscription; String text; String failure; boolean done; String aktion; String grund;
        Fragment(UUID id, CancellationToken token) { this.id = id; this.token = token; this.startedNanos = System.nanoTime(); }
    }
    /** Weiterreden innerhalb dieser Zeit (solange Jarvis noch nicht spricht) ergänzt die Frage statt sie abzubrechen. */
    static final long CONTINUE_NANOS = 25_000_000_000L;
    private final LinkedHashMap<UUID, Fragment> fragments = new LinkedHashMap<>();
    private String carryText = "";
    private long carryNanos;
    /** Aufnahmen, die begannen, während Jarvis hörbar sprach: Er läuft leiser weiter, bis der Türsteher entscheidet. */
    private final Set<UUID> tentative = new HashSet<>();
    /** Was Jarvis zuletzt gesagt hat (auch Zwischenstand): Kontext für den Sprach-Türsteher. */
    private String lastAssistantText = "";
    /** Kontext je Aufnahme, beim Sprachbeginn eingefroren: der Text läuft der hörbaren Wiedergabe sonst voraus. */
    private final Map<UUID, String> contextAtStart = new LinkedHashMap<>();
    private String currentPrompt;
    private long turnStartedNanos;
    private boolean turnSpeaking;
    private UUID audioUtterance;
    private Double lastSttMs;
    private String status = "idle";
    private String selectedCamera;
    private Map<String, Object> view;
    private Disposable metricsTask;
    private long lastStopAckMs;

    /** Compatibility constructor used by focused unit tests. */
    public Room(String id, int maxClients, int maxFrames, GenerationProvider generation,
                TtsProvider tts, ObjectMapper mapper) {
        this(id, maxClients, maxFrames, 8_000, true, generation, tts, null, mapper,
                Protocol.defaultCities(), Protocol.defaultCameras());
    }

    public Room(String id, int maxClients, int maxFrames, GenerationProvider generation,
                TtsProvider tts, SttProvider stt, ObjectMapper mapper) {
        this(id, maxClients, maxFrames, 8_000, true, generation, tts, stt, mapper,
                Protocol.defaultCities(), Protocol.defaultCameras());
    }

    public Room(String id, JarvisProperties props, GenerationProvider generation,
                TtsProvider tts, SttProvider stt, ObjectMapper mapper) {
        this(id, props.getMaxClientsPerRoom(), props.getMaxOutputFrames(), props.getMaxInputChars(),
                props.isDemo(), generation, tts, stt, mapper, Protocol.defaultCities(), props.getCameras());
    }

    public Room(String id, int maxClients, int maxFrames, int maxInputChars, boolean demo,
                GenerationProvider generation, TtsProvider tts, SttProvider stt, ObjectMapper mapper,
                List<Protocol.City> cities, List<Protocol.Camera> cameras) {
        if (!Protocol.validId(id)) throw new IllegalArgumentException("ungültige room id");
        if (maxClients < 1 || maxFrames < 2) throw new IllegalArgumentException("room limits");
        this.id = id;
        this.maxClients = maxClients;
        this.maxInputChars = Math.max(1, maxInputChars);
        this.demo = demo;
        this.generation = generation;
        this.tts = tts;
        this.stt = stt;
        this.mapper = mapper;
        this.ring = new AudioRingBuffer(maxFrames);
        this.audioSink = new AtomicReference<>(newAudioSink(maxFrames));
        this.cities = List.copyOf(cities == null ? Protocol.defaultCities() : cities);
        this.cameras = List.copyOf(cameras == null ? Protocol.defaultCameras() : cameras);
        this.ring.setEpoch(0);
    }

    public String id() { return id; }
    public synchronized long epoch() { return epoch; }
    public synchronized String owner() { return owner; }
    public synchronized String status() { return status; }
    public synchronized UUID streamId() { return streamId; }
    public synchronized long interrupts() { return interrupts.get(); }
    public synchronized int clientCount() { return clients.size(); }
    public synchronized boolean has(String client) { return client != null && clients.containsKey(client); }
    public synchronized boolean isOwner(String client) { return client != null && client.equals(owner); }

    /**
     * Prüft den Besitzernachweis für Schreibzugriffe über HTTP: das Ticket muss dem des verbundenen Besitzers
     * entsprechen UND die Anfrage muss über dieselbe Session kommen. Beobachter kennen das Ticket nicht.
     */
    public synchronized boolean ownerTicketValid(String ticket, String sessionKey) {
        if (owner == null || ownerTicket == null || ticket == null || sessionKey == null || sessionKey.isEmpty()) return false;
        if (ticket.length() > 128 || !clients.containsKey(owner)) return false;
        boolean sameTicket = java.security.MessageDigest.isEqual(ticket.getBytes(java.nio.charset.StandardCharsets.UTF_8),
                ownerTicket.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        boolean sameSession = java.security.MessageDigest.isEqual(sessionKey.getBytes(java.nio.charset.StandardCharsets.UTF_8),
                ownerSession.getBytes(java.nio.charset.StandardCharsets.UTF_8));
        return sameTicket && sameSession;
    }

    /** Für Tests und Diagnose: das Ticket nur für den genannten Besitzer, sonst null. */
    public synchronized String ownerTicketFor(String client) { return isOwner(client) ? ownerTicket : null; }

    public synchronized Client join(String client, String screen) { return join(client, screen, "lokal"); }

    public synchronized Client join(String client, String screen, String sessionKey) {
        if (!Protocol.validId(client)) throw new IllegalArgumentException("ungültiger client");
        if (clients.containsKey(client)) throw new IllegalStateException("Client ist bereits verbunden");
        if (clients.size() >= maxClients) throw new IllegalStateException("Raum ist voll");
        Screen parsed;
        if ("main".equalsIgnoreCase(screen)) parsed = Screen.MAIN;
        else if ("ops".equalsIgnoreCase(screen)) parsed = Screen.OPS;
        else throw new IllegalArgumentException("screen muss main oder ops sein");
        Client clientRecord = new Client(client, parsed, Sinks.many().unicast()
                .onBackpressureBuffer(new java.util.concurrent.ArrayBlockingQueue<>(CONTROL_QUEUE_LIMIT)));
        clients.put(client, clientRecord);
        if (parsed == Screen.MAIN && owner == null) {
            owner = client;
            byte[] bytes = new byte[32];
            TICKETS.nextBytes(bytes);
            ownerTicket = java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(bytes);
            ownerSession = sessionKey == null ? "" : sessionKey;
        }
        send(clientRecord, welcome(clientRecord));
        startMetricsIfNeeded();
        return clientRecord;
    }

    public synchronized void leave(String client) {
        Client removed = clients.remove(client);
        if (removed == null) return;
        removed.control().tryEmitComplete();
        if (client.equals(audioOwner)) audioOwner = null;
        if (client.equals(owner)) {
            stopCurrentLocked("disconnect");
            cancelSttLocked();
            Sinks.Many<AudioFrame> departed = audioSink.getAndSet(newAudioSink(ring.capacity()));
            departed.tryEmitComplete();
            owner = null;
            ownerTicket = null;
            ownerSession = null;
        }
        if (clients.isEmpty() && metricsTask != null) {
            metricsTask.dispose();
            metricsTask = null;
        }
    }

    /** Claims the single output connection; operations clients can never claim it. */
    public synchronized boolean claimAudio(String client) {
        if (!isOwner(client)) return false;
        if (audioOwner != null) return false;
        audioOwner = client;
        return true;
    }

    public synchronized void releaseAudio(String client) {
        if (client != null && client.equals(audioOwner)) {
            audioOwner = null;
            stopCurrentLocked("audio_disconnect");
            cancelSttLocked();
            Sinks.Many<AudioFrame> old = audioSink.getAndSet(newAudioSink(ring.capacity()));
            old.tryEmitComplete();
        }
    }

    public Flux<String> controlFlux(Client client) { return client.control().asFlux(); }

    /** Stable across STOP/new-turn; stale frames are filtered by the room epoch. */
    public Flux<AudioFrame> audioFlux() {
        Sinks.Many<AudioFrame> sink = audioSink.get();
        return sink.asFlux().filter(frame -> {
            synchronized (Room.this) { return frame.epoch() == epoch; }
        });
    }

    /** Monotone Serverzeit in ms seit Start; Grundlage aller TRACE-Punkte und des Uhrabgleichs im Browser. */
    double nowMs() { return Math.round((System.nanoTime() - startedAt) / 100_000.0d) / 10.0d; }

    public void ping(Client client, double sentAt) {
        if (client == null || !has(client.id())) return;
        Map<String, Object> pong = new LinkedHashMap<>();
        pong.put("type", "PONG"); pong.put("sent_at", sentAt); pong.put("server_t_ms", nowMs());
        send(client, json(pong));
    }

    /** Ein gemessener Punkt der Sprachkette. Nur Ereignisse, die hier tatsächlich passieren; nichts wird geschätzt. */
    private void traceLocked(String point, UUID stream, UUID utterance, Map<String, ?> extra) {
        traceAtLocked(point, nowMs(), stream, utterance, extra);
    }

    private void traceAtLocked(String point, double tMs, UUID stream, UUID utterance, Map<String, ?> extra) {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("point", point);
        fields.put("t_ms", tMs);
        fields.put("stream_id", stream == null ? null : stream.toString());
        fields.put("utterance_id", utterance == null ? null : utterance.toString());
        if (extra != null) fields.putAll(extra);
        broadcastLocked(event("TRACE", fields));
    }

    private record Sentence(int index, String text) { }

    public void start(String client, String prompt) {
        startTurn(client, prompt, true, List.of());
    }

    private void startTurn(String client, String prompt, boolean typed, List<UUID> utterances) {
        final UUID id;
        final long turnEpoch;
        final CancellationToken token;
        synchronized (this) {
            requireOwner(client, "USER_TEXT");
            if (prompt == null || prompt.isBlank() || prompt.length() > maxInputChars) {
                throw new IllegalArgumentException("USER_TEXT ist leer oder zu lang");
            }
            // A typed turn supersedes microphone work before generation is subscribed.
            if (typed) cancelSttLocked();
            UUID superseded = streamId;
            stopCurrentLocked("new_turn");
            id = UUID.randomUUID();
            streamId = id;
            turnEpoch = ++epoch;
            nextSequence = 0;
            ring.setEpoch(turnEpoch);
            token = new CancellationToken();
            generationToken = token;
            status = "thinking";
            currentPrompt = prompt;
            turnStartedNanos = System.nanoTime();
            turnSpeaking = false;
            addTranscriptLocked("user-" + id, "user", prompt, id, true);
            emitStateLocked("thinking", id);
            Map<String, Object> start = new LinkedHashMap<>();
            start.put("stream_id", id.toString());
            start.put("source", typed ? "text" : "voice");
            start.put("utterance_ids", utterances.stream().map(UUID::toString).toList());
            start.put("supersedes", superseded == null ? null : superseded.toString());
            start.put("t_ms", nowMs());
            broadcastLocked(event("TURN_START", start));
            traceLocked("turn_dispatch", id, null, Map.of("source", typed ? "text" : "voice"));
        }

        SentenceSplitter splitter = new SentenceSplitter();
        StringBuilder assistant = new StringBuilder();
        GenerationContext context = new GenerationContext(id(), id,
                event -> generationEvent(id, turnEpoch, token, event));
        Flux<String> generated = Flux.defer(() -> {
            token.checkpoint();
            return generation.generate(prompt, token, context);
        });
        long[] lastTranscript = { 0L };
        int[] sentenceCount = { 0 };
        boolean[] firstToken = { true };
        Flux<Void> work = generated.concatMap(chunk -> {
            if (chunk == null || !isCurrent(id, turnEpoch, token)) return Flux.empty();
            List<Sentence> sentences = new ArrayList<>();
            synchronized (Room.this) {
                if (!isCurrentLocked(id, turnEpoch, token)) return Flux.empty();
                if (firstToken[0] && !chunk.isEmpty()) {
                    firstToken[0] = false;
                    traceLocked("llm_first_token", id, null, null);
                }
                assistant.append(chunk);
                for (String sentence : splitter.accept(chunk)) {
                    Sentence indexed = new Sentence(++sentenceCount[0], sentence);
                    sentences.add(indexed);
                    traceLocked("sentence_ready", id, null, Map.of("n", indexed.index()));
                }
                // Zwischenstand nur am Satzende oder alle 150 ms; der vollständige Text folgt am Ende (final).
                long now = System.nanoTime();
                if (!sentences.isEmpty() || now - lastTranscript[0] >= TRANSCRIPT_INTERVAL_NANOS) {
                    lastTranscript[0] = now;
                    addTranscriptLocked("assistant-" + id, "assistant", assistant.toString(), id, false);
                }
            }
            return Flux.fromIterable(sentences);
        })
          // Bis zu 2 Sätze werden gleichzeitig vertont (der nächste ist fertig, während der erste läuft);
          // flatMapSequential liefert den Ton trotzdem streng in Satzreihenfolge aus.
          .flatMapSequential(sentence -> audioFor(id, turnEpoch, token, sentence), TTS_PREFETCH)
          .concatWith(Flux.defer(() -> {
              String tail = splitter.flush();
              if (tail.isBlank()) return Flux.empty();
              Sentence last;
              synchronized (Room.this) {
                  if (!isCurrentLocked(id, turnEpoch, token)) return Flux.empty();
                  last = new Sentence(++sentenceCount[0], tail);
                  traceLocked("sentence_ready", id, null, Map.of("n", last.index()));
              }
              return audioFor(id, turnEpoch, token, last);
          }))
          .doOnNext(chunk -> emitAudio(id, turnEpoch, chunk, token))
          .thenMany(Flux.empty());

        Disposable subscription = work.subscribe(ignored -> { }, error -> generationFailed(id, turnEpoch, token, error),
                () -> generationFinished(id, turnEpoch, token, assistant.toString()));
        synchronized (this) {
            if (isCurrentLocked(id, turnEpoch, token)) generationSubscription = subscription;
            else subscription.dispose();
        }
    }

    private void generationEvent(UUID id, long turnEpoch, CancellationToken token, Map<String, Object> event) {
        synchronized (this) {
            if (!isCurrentLocked(id, turnEpoch, token) || event == null) return;
            String type = String.valueOf(event.getOrDefault("type", "LOG"));
            if ("MEMORY".equals(type)) broadcastLocked(event("MEMORY", event));
            else if ("TOOL".equals(type)) broadcastLocked(event("TOOL", Map.of("stream_id", id.toString(),
                    "name", Protocol.safe(String.valueOf(event.getOrDefault("name", "")), 64))));
            else if ("LOG".equals(type)) addLogLocked(String.valueOf(event.getOrDefault("level", "info")),
                    String.valueOf(event.getOrDefault("message", "")));
        }
    }

    /** Erzeugt nur den Ton eines Satzes; ausgeliefert wird er geordnet in start(). Abgebrochene Züge liefern nichts. */
    private Flux<de.jarvis.backend.provider.TtsChunk> audioFor(UUID id, long turnEpoch, CancellationToken token, Sentence sentence) {
        return Flux.defer(() -> {
            synchronized (Room.this) {
                if (!isCurrentLocked(id, turnEpoch, token)) return Flux.<de.jarvis.backend.provider.TtsChunk>empty();
                traceLocked("tts_request", id, null, Map.of("n", sentence.index()));
            }
            long begun = System.nanoTime();
            boolean[] first = {true};
            return tts.stream(sentence.text(), token).filter(chunk -> isCurrent(id, turnEpoch, token))
                    .doOnNext(chunk -> {
                        if (first[0]) {
                            first[0] = false;
                            recordTtsFirstChunk((System.nanoTime() - begun) / 1_000_000L);
                            synchronized (Room.this) {
                                if (isCurrentLocked(id, turnEpoch, token))
                                    traceLocked("tts_first_chunk", id, null, Map.of("n", sentence.index()));
                            }
                        }
                    })
                    // Ein einzelner Satz ohne Ton darf nicht die ganze Antwort abbrechen: Text bleibt sichtbar.
                    .onErrorResume(error -> {
                        synchronized (Room.this) {
                            if (isCurrentLocked(id, turnEpoch, token)) {
                                addLogLocked("warn", "Ein Satz konnte nicht vertont werden; Antwort läuft weiter.");
                                traceLocked("tts_failed", id, null, Map.of("n", sentence.index()));
                            }
                        }
                        return Flux.empty();
                    });
        });
    }

    private void emitAudio(UUID id, long turnEpoch, de.jarvis.backend.provider.TtsChunk chunk, CancellationToken token) {
        byte[] bytes = chunk == null ? null : chunk.bytes();
        if (bytes == null || bytes.length == 0 || bytes.length > 2_000_000) return;
        synchronized (this) {
            if (!isCurrentLocked(id, turnEpoch, token)) return;
            String mime = chunk.mime();
            if (!de.jarvis.backend.provider.TtsChunk.allowedMime(mime)) mime = "audio/mpeg";
            if (!turnSpeaking) traceLocked("first_audio_sent", id, null, null);
            turnSpeaking = true;
            if (!"speaking".equals(status)) emitStateLocked("speaking", id);
            AudioFrame frame = new AudioFrame(id, turnEpoch, nextSequence++, mime, bytes);
            if (!ring.offer(frame)) {
                droppedMessages.incrementAndGet();
                return;
            }
            Sinks.EmitResult result = audioSink.get().tryEmitNext(frame);
            // The replay sink is the delivery queue; the ring is consumed at publish time.
            ring.poll();
            if (result.isFailure()) droppedMessages.incrementAndGet();
        }
    }

    /** Compatibility overload: stop the current stream as the current owner. */
    public synchronized boolean stop(String requestedStream, String reason) {
        return stop(owner, requestedStream, reason);
    }

    /** Owner-scoped stop; a stale/non-owner caller cannot stop another client's stream. */
    public synchronized boolean stop(String client, String requestedStream, String reason) {
        requireOwner(client, "STOP_STREAM");
        if (requestedStream != null && (streamId == null || !requestedStream.equals(streamId.toString()))) return false;
        if ("vad".equals(reason)) return interruptForSpeechLocked();  // Sprachbeginn: evtl. Fortsetzung, nie Erkennung verwerfen
        boolean stopped = stopCurrentLocked(reason == null ? "manual" : Protocol.safe(reason, 32));
        if (requestedStream == null && !fragments.isEmpty()) cancelSttLocked();
        return stopped;
    }

    private boolean stopCurrentLocked(String reason) {
        UUID old = streamId;
        if (old == null) return false;
        long start = System.nanoTime();
        ++epoch;
        ring.setEpoch(epoch);
        audioQueue.get().clear();
        ring.clearBuffer();
        interrupts.incrementAndGet();
        if (generationToken != null) generationToken.cancel();
        if (generationSubscription != null) generationSubscription.dispose();
        generationToken = null;
        generationSubscription = null;
        streamId = null;
        status = "idle";
        long ackMs = (System.nanoTime() - start) / 1_000_000L;
        lastStopAckMs = ackMs;
        traceLocked("stop", old, null, Map.of("reason", Protocol.safe(reason, 32)));
        broadcastLocked(event("STREAM_STOPPED", Map.of("stream_id", old.toString(), "reason", Protocol.safe(reason, 32), "ack_ms", ackMs)));
        emitStateLocked("idle", old);
        return true;
    }

    private boolean isCurrent(UUID id, long turnEpoch, CancellationToken token) {
        synchronized (this) { return isCurrentLocked(id, turnEpoch, token); }
    }

    private boolean isCurrentLocked(UUID id, long turnEpoch, CancellationToken token) {
        return streamId != null && streamId.equals(id) && epoch == turnEpoch && generationToken == token && !token.isCancelled();
    }

    private void generationFinished(UUID id, long turnEpoch, CancellationToken token, String assistant) {
        synchronized (this) {
            if (!isCurrentLocked(id, turnEpoch, token)) return;
            addTranscriptLocked("assistant-" + id, "assistant", assistant, id, true);
            traceLocked("generation_done", id, null, null);
            status = "idle";
            broadcastLocked(event("TURN_END", Map.of("stream_id", id.toString())));
            emitStateLocked("idle", id);
            // Keep the playback generation addressable after text/TTS generation ends.
            // Browser audio may still be scheduled and must accept a scoped STOP_STREAM.
            generationSubscription = null;
        }
    }

    private void generationFailed(UUID id, long turnEpoch, CancellationToken token, Throwable error) {
        synchronized (this) {
            if (token.isCancelled() || !isCurrentLocked(id, turnEpoch, token)) return;
            addLogLocked("error", "Providerfehler");
            broadcastLocked(errorEvent("llm", "Claude hat nicht geantwortet. " + safeError(error), id, null));
            stopCurrentLocked("error");
        }
    }

    /**
     * Neuer Sprachbeginn. Denkt Jarvis noch (hat noch nicht gesprochen) und ist der Zug jung, redet der Nutzer
     * offensichtlich weiter: die laufende Antwort wird abgebrochen, ihre Frage aber als Anfang übernommen.
     * Spricht Jarvis schon, ist es eine echte Unterbrechung (Barge-in) ohne Übernahme.
     */
    private boolean interruptForSpeechLocked() {
        if (streamId == null) return false;
        if (!turnSpeaking && "thinking".equals(status) && currentPrompt != null
                && System.nanoTime() - turnStartedNanos < CONTINUE_NANOS) {
            carryText = currentPrompt;
            carryNanos = System.nanoTime();
            addLogLocked("info", "Weitergesprochen: Frage wird ergänzt statt abgebrochen.");
            return stopCurrentLocked("continuation");
        }
        return stopCurrentLocked("vad");
    }

    /** Begins microphone capture; earlier fragments keep their recognition running. */
    public synchronized void audioStart(String client, UUID utterance, int sampleRate) {
        audioStart(client, utterance, sampleRate, false);
    }

    /**
     * Begins microphone capture. {@code tentative}: der Browser hört Jarvis noch und hat ihn nur leiser gestellt –
     * der laufende Zug bleibt, bis der Sprach-Türsteher über die Äußerung entschieden hat.
     */
    public synchronized void audioStart(String client, UUID utterance, int sampleRate, boolean tentativeStart) {
        requireOwner(client, "AUDIO_START");
        if (sampleRate != 16_000 || utterance == null) throw new IllegalArgumentException("AUDIO_START ist ungültig");
        contextAtStart.put(utterance, lastAssistantText);
        while (contextAtStart.size() > 16) contextAtStart.remove(contextAtStart.keySet().iterator().next());
        if (tentativeStart) {
            tentative.add(utterance);
            traceLocked("barge_tentative", streamId, utterance, null);
        } else interruptForSpeechLocked();
        audioUtterance = utterance;
        status = "listening";
        emitStateLocked("listening", null, utterance);
    }

    public synchronized Mono<String> transcribe(String client, UUID utterance, byte[] pcm) {
        requireOwner(client, "AUDIO_END");
        if (utterance == null || pcm == null || pcm.length < 6_400 || pcm.length > MAX_AUDIO_BYTES
                || pcm.length % 640 != 0) throw new IllegalArgumentException("PCM-Aufnahme hat ungültige Länge");
        if (stt == null) throw new IllegalStateException("STT ist nicht konfiguriert");
        if (fragments.size() >= 6) {  // begrenzt: älteste offene Erkennung aufgeben
            Fragment oldest = fragments.values().iterator().next();
            oldest.token.cancel();
            if (oldest.subscription != null) oldest.subscription.dispose();
            fragments.remove(oldest.id);
            if (tentative.remove(oldest.id)) bargeIgnoredLocked(oldest.id, "verdraengt");
        }
        traceLocked("audio_end_received", null, utterance, Map.of("audio_ms", pcm.length / 32));
        Fragment fragment = new Fragment(utterance, new CancellationToken());
        fragments.put(utterance, fragment);
        if (utterance.equals(audioUtterance)) audioUtterance = null;  // Aufnahme beendet
        status = "transcribing";
        emitStateLocked("transcribing", null, utterance);
        CancellationToken token = fragment.token;
        double sentMs = nowMs();
        // Java hält Fragmente nicht zurück und wiederholt nicht: erster Sendeversuch = Senden.
        traceAtLocked("stt_queued", sentMs, null, utterance, null);
        traceAtLocked("stt_sent", sentMs, null, utterance, null);
        // „Jarvis ist dran“: er spricht (vorläufiges Reinreden) oder seine Antwort wurde für diese Äußerung angehalten.
        String kontext = contextAtStart.remove(utterance);
        SttProvider.GateContext gate = new SttProvider.GateContext(kontext != null ? kontext : lastAssistantText,
                tentative.contains(utterance) || !carryText.isBlank());
        Mono<String> request = stt.transcribeTimed(pcm.clone(), token, gate)
                .doOnCancel(token::cancel)
                .doOnNext(result -> sttTimings(utterance, token, sentMs, result))
                .doOnNext(result -> fragmentDone(client, utterance, token, result))
                .map(result -> result.text() == null ? "" : result.text())
                .doOnError(error -> fragmentFailed(client, utterance, token, error));
        fragment.subscription = request.subscribe(text -> { }, error -> { }, () -> { });
        return request;
    }

    public synchronized void audioAbort(String client, UUID utterance) {
        requireOwner(client, "AUDIO_ABORT");
        if (utterance != null && audioUtterance != null && !utterance.equals(audioUtterance)) return;
        audioUtterance = null;
        if (utterance != null && tentative.remove(utterance)) {
            bargeIgnoredLocked(utterance, "verworfen");
            if (fragments.isEmpty() && carryText.isEmpty()) return;
        }
        if (fragments.isEmpty() && carryText.isEmpty()) {
            status = "idle";
            emitStateLocked("idle", null);
            return;
        }
        // Verworfene Zusatzaufnahme (Geräusch, zu kurz): die bereits gestellte Frage trotzdem beantworten –
        // sonst bleibt der Raum auf „hört zu“ stehen und die Frage geht verloren.
        dispatchLater(client);
    }

    /**
     * Start/Ende des Dekodierens auf der Serveruhr. Die Bridge misst Wartezeit und Dekodierdauer selbst; der Rest der
     * Gesamtdauer ist Netz hin und zurück und wird symmetrisch verteilt. Ohne Bridge-Zeiten gibt es nur stt_received.
     */
    private void sttTimings(UUID utterance, CancellationToken token, double sentMs, SttProvider.SttResult result) {
        synchronized (this) {
            Fragment fragment = fragments.get(utterance);
            if (fragment == null || fragment.token != token || token.isCancelled()) return;
            double received = nowMs();
            if (result.bridgeWaitMs() != null && result.decodeMs() != null) {
                double net = Math.max(0, (received - sentMs) - result.bridgeWaitMs() - result.decodeMs());
                double start = Math.round((sentMs + net / 2 + result.bridgeWaitMs()) * 10) / 10.0d;
                double end = Math.min(received, Math.round((start + result.decodeMs()) * 10) / 10.0d);
                traceAtLocked("stt_start", start, null, utterance, Map.of("derived", true));
                traceAtLocked("stt_end", end, null, utterance, Map.of("derived", true, "decode_ms", result.decodeMs()));
            }
            traceAtLocked("stt_received", received, null, utterance, null);
        }
    }

    public synchronized void audioRejected(String client, UUID utterance, String message) {
        requireOwner(client, "AUDIO_END");
        if (utterance != null && audioUtterance != null && !utterance.equals(audioUtterance)) return;
        audioUtterance = null;
        if (utterance != null && tentative.remove(utterance)) {
            bargeIgnoredLocked(utterance, "still");  // Jarvis spricht weiter, ohne Fehlermeldung
            if (!fragments.isEmpty() || !carryText.isEmpty()) dispatchLater(client);
            return;
        }
        if (!fragments.isEmpty() || !carryText.isEmpty()) {
            dispatchLater(client);  // stille Zusatzaufnahme: bereits Gesagtes trotzdem beantworten
            return;
        }
        broadcastLocked(errorEvent("audio", message, null, utterance));
        status = "idle";
        emitStateLocked("idle", null);
    }

    private void fragmentDone(String client, UUID utterance, CancellationToken token, SttProvider.SttResult result) {
        synchronized (this) {
            Fragment fragment = fragments.get(utterance);
            if (fragment == null || fragment.token != token || token.isCancelled()) return;
            String text = result.text();
            fragment.text = text == null ? "" : text.trim();
            fragment.done = true;
            fragment.aktion = result.aktion();
            fragment.grund = result.grund();
            lastSttMs = Math.max(0.001d, (System.nanoTime() - fragment.startedNanos) / 1_000_000.0d);
            boolean wasTentative = tentative.remove(utterance);
            // „Ignorieren“ gilt nur für eine Äußerung, die allein steht. Gehört sie zu einer Frage, die schon läuft
            // (weitere Abschnitte, angehaltene Frage), ist sie deren Fortsetzung und wird mit beantwortet.
            boolean partOfQuestion = !carryText.isBlank()
                    || fragments.values().stream().anyMatch(f -> f != fragment);
            if ("ignorieren".equals(fragment.aktion) && partOfQuestion) fragment.aktion = "antworten";
            if ("ignorieren".equals(fragment.aktion) || "stoppen".equals(fragment.aktion)) {
                fragments.remove(utterance);
                traceLocked("gate_" + fragment.aktion, streamId, utterance, Map.of("grund", Protocol.safe(String.valueOf(fragment.grund), 48)));
                // Sichtbar machen, warum Jarvis nicht antwortet (ohne den erkannten Text).
                broadcastLocked(event("GATE", Map.of("utterance_id", utterance.toString(), "aktion", fragment.aktion,
                        "grund", Protocol.safe(String.valueOf(fragment.grund), 48))));
                if ("stoppen".equals(fragment.aktion)) {
                    addLogLocked("info", "Gestoppt auf Zuruf.");
                    stopCurrentLocked("manual");
                    // „Stopp“ beendet auch die angehaltene Frage und ihre Abschnitte – nicht aber eine Aufnahme,
                    // die gerade schon läuft (der Nutzer redet weiter).
                    for (Fragment f : fragments.values()) {
                        f.token.cancel();
                        if (f.subscription != null) f.subscription.dispose();
                        if (tentative.remove(f.id)) bargeIgnoredLocked(f.id, "gestoppt");
                    }
                    fragments.clear();
                    carryText = "";
                    if (audioUtterance == null) emitStateLocked("idle", null);
                    return;
                }
                addLogLocked("info", "Nicht an Jarvis gerichtet, ignoriert (" + fragment.grund + ").");
                if (wasTentative) bargeIgnoredLocked(utterance, "tuersteher");
                if (audioUtterance == null) {
                    // Spricht er noch (Antwort wird erzeugt), zeigt der Raum wieder „spricht“, sonst „bereit“.
                    boolean generating = streamId != null && generationSubscription != null && turnSpeaking;
                    emitStateLocked(generating ? "speaking" : "idle", generating ? streamId : null);
                }
                return;
            } else if (wasTentative) {
                stopCurrentLocked("vad");  // echtes Reinreden: jetzt erst unterbrechen
            }
        }
        dispatchLater(client);
    }

    /** Vorläufiges Reinreden ohne Folgen: der Browser stellt Jarvis wieder auf volle Lautstärke. */
    private void bargeIgnoredLocked(UUID utterance, String reason) {
        broadcastLocked(event("BARGE_IGNORED", Map.of("utterance_id", utterance.toString(), "reason", Protocol.safe(reason, 32))));
        traceLocked("barge_ignored", streamId, utterance, Map.of("reason", Protocol.safe(reason, 32)));
    }

    private void fragmentFailed(String client, UUID utterance, CancellationToken token, Throwable error) {
        synchronized (this) {
            Fragment fragment = fragments.get(utterance);
            if (fragment == null || fragment.token != token || token.isCancelled()) return;
            fragment.text = "";
            fragment.done = true;
            fragment.failure = sttError(error);
            if (tentative.remove(utterance)) bargeIgnoredLocked(utterance, "stt_fehler");
            traceLocked(error instanceof SttProvider.SttRejected ? "stt_rejected" : "stt_failed", null, utterance,
                    Map.of("message", fragment.failure));
            addLogLocked("warn", "Ein Teil der Aufnahme konnte nicht erkannt werden: " + fragment.failure);
        }
        dispatchLater(client);
    }

    /** Fragt erst, wenn der Nutzer fertig ist: keine laufende Aufnahme, alle Erkennungen fertig. Dann alles in Reihenfolge. */
    private void dispatchLater(String client) {
        String combined;
        List<UUID> utterances = new ArrayList<>();
        synchronized (this) {
            if (audioUtterance != null) return;                        // er spricht gerade weiter
            if (fragments.values().stream().anyMatch(f -> !f.done)) return;
            StringBuilder text = new StringBuilder();
            if (!carryText.isBlank() && System.nanoTime() - carryNanos < 2 * CONTINUE_NANOS) text.append(carryText);
            String failure = null;
            UUID failedUtterance = null;
            for (Fragment f : fragments.values()) {
                utterances.add(f.id);
                if (f.text != null && !f.text.isBlank()) text.append(text.length() > 0 ? " " : "").append(f.text);
                if (f.failure != null) { failure = f.failure; failedUtterance = f.id; }
            }
            fragments.clear();
            carryText = "";
            combined = text.toString().trim();
            if (combined.isEmpty() || !isOwner(client)) {
                // Fehlgeschlagene Erkennung nicht stumm verschlucken: der Nutzer soll wissen, warum nichts passiert.
                if (failure != null && isOwner(client)) broadcastLocked(errorEvent("stt", failure, null, failedUtterance));
                status = "idle";
                emitStateLocked("idle", null);
                return;
            }
        }
        startTurn(client, Protocol.safe(combined, maxInputChars), false, utterances);
    }

    private void cancelSttLocked() {
        for (Fragment f : fragments.values()) {
            f.token.cancel();
            if (f.subscription != null) f.subscription.dispose();
        }
        fragments.clear();
        carryText = "";
        audioUtterance = null;
        tentative.clear();
    }

    public synchronized void view(Client client, Map<String, Object> message) {
        requireOwner(client.id(), "VIEW_SET");
        Map<String, Object> next = new LinkedHashMap<>();
        if (message.containsKey("city_id")) {
            String cityId = String.valueOf(message.get("city_id"));
            Protocol.City city = cities.stream().filter(c -> c.id().equals(cityId)).findFirst()
                    .orElseThrow(() -> new IllegalArgumentException("Unbekannte Stadt"));
            next.put("city_id", city.id()); next.put("lat", city.lat()); next.put("lon", city.lon()); next.put("height", 1_000.0);
        } else {
            double lat = finiteNumber(message, "lat"), lon = finiteNumber(message, "lon"), height = finiteNumber(message, "height");
            if (lat < -90 || lat > 90 || lon < -180 || lon > 180 || height < 0 || height > 100_000_000) {
                throw new IllegalArgumentException("VIEW außerhalb des gültigen Bereichs");
            }
            next.put("lat", lat); next.put("lon", lon); next.put("height", height);
        }
        view = Map.copyOf(next);
        broadcastLocked(event("VIEW", next));
    }

    public synchronized void camera(Client client, Map<String, Object> message) {
        requireOwner(client.id(), "CAMERA_SELECT");
        Object raw = message.get("camera_id");
        String camera = raw == null ? null : String.valueOf(raw);
        if (camera != null && cameras.stream().noneMatch(c -> c.id().equals(camera))) {
            throw new IllegalArgumentException("Unbekannte Kamera");
        }
        selectedCamera = camera;
        Map<String, Object> event = new LinkedHashMap<>();
        event.put("camera_id", camera);
        broadcastLocked(event("CAMERA", event));
    }

    public synchronized void reset(String client) {
        requireOwner(client, "RESET");
        stopCurrentLocked("reset");
        cancelSttLocked();
        lastAssistantText = "";  // nach dem Zurücksetzen nichts aus dem alten Gespräch an den Türsteher
        contextAtStart.clear();
        transcriptHistory.clear();
        logHistory.clear();
        broadcastLocked(event("RESET_DONE", Map.of()));
    }

    private double finiteNumber(Map<String, Object> message, String field) {
        Object value = message.get(field);
        if (!(value instanceof Number number)) throw new IllegalArgumentException("VIEW Feld fehlt: " + field);
        double result = number.doubleValue();
        if (!Double.isFinite(result)) throw new IllegalArgumentException("VIEW Feld ist nicht endlich: " + field);
        return result;
    }

    private void requireOwner(String client, String operation) {
        if (!isOwner(client)) throw new IllegalStateException("Nur der Besitzer darf " + operation + " senden");
    }

    private synchronized String welcome(Client client) {
        Map<String, Object> snapshot = new LinkedHashMap<>();
        snapshot.put("demo", demo);
        snapshot.put("status", status);
        snapshot.put("stream_id", streamId == null ? null : streamId.toString());
        snapshot.put("view", view);
        snapshot.put("selected_camera", selectedCamera);
        snapshot.put("transcripts", List.copyOf(transcriptHistory.values()));
        snapshot.put("logs", List.copyOf(logHistory));
        snapshot.put("metrics", metrics());
        Map<String, Object> welcome = new LinkedHashMap<>();
        welcome.put("type", "WELCOME");
        welcome.put("role", isOwner(client.id()) ? "owner" : "observer");
        welcome.put("snapshot", snapshot);
        welcome.put("cities", cities);
        welcome.put("cameras", cameras);
        // Nur der Besitzer erhält das Ticket, und nur über seine eigene Steuerverbindung (nie per Broadcast).
        if (isOwner(client.id())) welcome.put("owner_ticket", ownerTicket);
        return json(welcome);
    }

    /** Zeit vom Satzbeginn der Vertonung bis zum ersten Ton-Stück (letzte 50 Sätze). */
    private final java.util.ArrayDeque<Long> ttsFirstChunkMs = new java.util.ArrayDeque<>();

    private synchronized void recordTtsFirstChunk(long ms) {
        if (ttsFirstChunkMs.size() >= 50) ttsFirstChunkMs.removeFirst();
        ttsFirstChunkMs.addLast(ms);
    }

    static Long percentile(java.util.Collection<Long> values, int p) {
        if (values.isEmpty()) return null;
        java.util.List<Long> sorted = new java.util.ArrayList<>(values);
        java.util.Collections.sort(sorted);
        int index = (int) Math.ceil(p / 100.0 * sorted.size()) - 1;
        return sorted.get(Math.max(0, Math.min(sorted.size() - 1, index)));
    }

    private synchronized Map<String, Object> metrics() {
        Runtime runtime = Runtime.getRuntime();
        long used = runtime.totalMemory() - runtime.freeMemory();
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("uptime_s", (System.nanoTime() - startedAt) / 1_000_000_000L);
        result.put("event_loop_lag_ms", null);
        result.put("rooms", 1);
        result.put("clients", clients.size());
        result.put("interrupts", interrupts.get());
        result.put("stop_ack_ms", lastStopAckMs == 0 ? null : lastStopAckMs);
        result.put("stt_ms", lastSttMs);
        result.put("tts_ms", percentile(ttsFirstChunkMs, 50));
        result.put("tts_p95_ms", percentile(ttsFirstChunkMs, 95));
        result.put("dropped_messages", droppedMessages.get());
        result.put("jvm_heap_used_mb", used / 1024 / 1024);
        result.put("jvm_threads", threadMx.getThreadCount());
        return result;
    }

    private synchronized void startMetricsIfNeeded() {
        if (metricsTask != null && !metricsTask.isDisposed()) return;
        metricsTask = Flux.interval(Duration.ofSeconds(1), Schedulers.parallel()).subscribe(tick -> {
            synchronized (Room.this) {
                if (!clients.isEmpty()) broadcastLocked(event("METRICS", metrics()));
            }
        });
    }

    private void addTranscriptLocked(String id, String role, String text, UUID stream, boolean fin) {
        if ("assistant".equals(role) && text != null && !text.isBlank()) lastAssistantText = text;
        Map<String, Object> item = new LinkedHashMap<>();
        item.put("id", id); item.put("role", role); item.put("text", Protocol.safe(text, maxInputChars));
        item.put("stream_id", stream.toString()); item.put("final", fin);
        transcriptHistory.remove(id);
        transcriptHistory.put(id, Map.copyOf(item));
        while (transcriptHistory.size() > HISTORY_LIMIT) transcriptHistory.remove(transcriptHistory.keySet().iterator().next());
        broadcastLocked(event("TRANSCRIPT", item));
    }

    private void addLogLocked(String level, String message) {
        Map<String, Object> item = new LinkedHashMap<>();
        item.put("id", UUID.randomUUID().toString()); item.put("time", Instant.now().toString());
        item.put("level", Protocol.safe(level, 16)); item.put("message", Protocol.safe(message, 240));
        logHistory.add(Map.copyOf(item));
        while (logHistory.size() > HISTORY_LIMIT) logHistory.remove(0);
        broadcastLocked(event("LOG", item));
    }

    private void emitStateLocked(String nextStatus, UUID stream) { emitStateLocked(nextStatus, stream, null); }

    private void emitStateLocked(String nextStatus, UUID stream, UUID utterance) {
        status = nextStatus;
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("status", nextStatus); fields.put("stream_id", stream == null ? null : stream.toString());
        if (utterance != null) fields.put("utterance_id", utterance.toString());
        fields.put("t_ms", nowMs());
        broadcastLocked(event("STATE", fields));
    }

    /** Fehler mit Stufe der Kette (audio, stt, llm, tts, connection), damit die Oberfläche sagt, wo es hakt. */
    private Map<String, Object> errorEvent(String stage, String message, UUID stream, UUID utterance) {
        Map<String, Object> fields = new LinkedHashMap<>();
        fields.put("message", Protocol.safe(message, 240));
        fields.put("stage", stage);
        fields.put("stream_id", stream == null ? null : stream.toString());
        fields.put("utterance_id", utterance == null ? null : utterance.toString());
        fields.put("t_ms", nowMs());
        return event("ERROR", fields);
    }

    /** Eigene, feste Meldungen des STT-Anbieters weitergeben; alles andere bleibt allgemein. */
    private static String sttError(Throwable error) {
        if ((error instanceof IllegalStateException || error instanceof IllegalArgumentException)
                && error.getMessage() != null && !error.getMessage().isBlank())
            return Protocol.safe(error.getMessage(), 160);
        if (error instanceof java.util.concurrent.TimeoutException)
            return "Spracherkennung hat nicht rechtzeitig geantwortet (60 s).";
        return "Spracherkennung nicht erreichbar. Läuft die lokale Bridge?";
    }

    private Map<String, Object> event(String type, Map<String, ?> fields) {
        Map<String, Object> result = new LinkedHashMap<>();
        result.put("type", type); result.putAll(fields); return result;
    }

    private void broadcastLocked(Object message) {
        String serialized = message instanceof String text ? text : json(message);
        clients.values().forEach(client -> {
            if (client.control().tryEmitNext(serialized).isFailure()) {
                droppedMessages.incrementAndGet();
                client.control().tryEmitError(new IllegalStateException("Client-Rückstau"));
            }
        });
    }

    private void send(Client client, String message) {
        if (client.control().tryEmitNext(message).isFailure()) droppedMessages.incrementAndGet();
    }

    private String json(Object value) {
        try { return mapper.writeValueAsString(value); }
        catch (Exception error) { return "{\"type\":\"ERROR\",\"message\":\"serialization failed\"}"; }
    }

    private Sinks.Many<AudioFrame> newAudioSink(int maxFrames) {
        var queue = new java.util.concurrent.ArrayBlockingQueue<AudioFrame>(maxFrames);
        audioQueue.set(queue);
        return Sinks.many().unicast().onBackpressureBuffer(queue);
    }

    private static String safeError(Throwable error) {
        return "Provider nicht verfügbar. Lokale Konfiguration und Verbindung prüfen.";
    }
}
