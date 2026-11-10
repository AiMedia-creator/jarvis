package de.jarvis.backend.config;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import de.jarvis.backend.protocol.Protocol;
import jakarta.annotation.PostConstruct;
import java.net.URI;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashSet;
import java.util.List;
import org.springframework.boot.context.properties.ConfigurationProperties;

@ConfigurationProperties(prefix = "jarvis")
public class JarvisProperties {
    private boolean demo = true;
    private String bind = "127.0.0.1";
    private boolean authRequired;
    private String sessionToken = "";
    private Boolean secureCookie;
    private List<String> allowedOrigins = new ArrayList<>(List.of("http://localhost:8080", "http://127.0.0.1:8080",
            "http://localhost:4173", "http://127.0.0.1:4173"));
    private String staticDir = "../dashboard/dist", camerasFile = "";
    private List<Protocol.Camera> cameras = Protocol.defaultCameras();
    private int maxRooms = 8, maxClientsPerRoom = 8, maxOutputFrames = 64, maxInputChars = 8000, maxSessionMinutes = 480;
    private String anthropicModel = "claude-sonnet-5-5", anthropicUrl = "https://api.anthropic.com/v1/messages";
    private String elevenlabsVoiceId = "21m00Tcm4TlvDq8ikWAM", elevenlabsModel = "eleven_multilingual_v2",
            elevenlabsUrl = "https://api.elevenlabs.io/v1/text-to-speech";
    private String sttUrl = "", sttApiKey = "";
    private String generationBackend = "python", bridgeUrl = "http://127.0.0.1:8766", bridgeToken = "";
    private int vadThreshold = 492;

    @PostConstruct
    public void validate() {
        if (isAuthRequired() && (sessionToken == null || sessionToken.length() < 16))
            throw new IllegalStateException("Auth benötigt JARVIS_SESSION_TOKEN mit mindestens 16 Zeichen.");
        if (maxRooms < 1 || maxRooms > 32 || maxClientsPerRoom < 1 || maxClientsPerRoom > 32
                || maxOutputFrames < 2 || maxOutputFrames > 128 || maxInputChars < 1 || maxInputChars > 8000)
            throw new IllegalStateException("Jarvis-Ressourcenlimits außerhalb des erlaubten Bereichs.");
        for (String origin : allowedOrigins) {
            URI uri = URI.create(origin);
            if (origin.contains("*") || uri.getHost() == null || uri.getUserInfo() != null
                    || !("http".equals(uri.getScheme()) || "https".equals(uri.getScheme()))
                    || uri.getQuery() != null || uri.getFragment() != null || !uri.getPath().isEmpty())
                throw new IllegalStateException("Origins müssen exakte HTTP(S)-Origins sein.");
        }
        validateEndpoint(anthropicUrl);
        validateEndpoint(elevenlabsUrl);
        if (!sttUrl.isBlank()) validateEndpoint(sttUrl);
        validateEndpoint(bridgeUrl);
        if (!List.of("python", "standalone").contains(generationBackend))
            throw new IllegalStateException("generation-backend muss python oder standalone sein.");
        if (!elevenlabsVoiceId.matches("[a-zA-Z0-9_-]{1,128}")) throw new IllegalStateException("Ungültige Voice-ID.");
        if (!camerasFile.isBlank()) {
            try {
                Path file = Path.of(camerasFile);
                if (Files.size(file) > 65536) throw new IllegalStateException("Kameradatei zu groß.");
                List<Protocol.Camera> loaded = new ObjectMapper().readValue(file.toFile(), new TypeReference<>() {});
                if (loaded == null || loaded.isEmpty() || loaded.size() > 32) throw new IllegalStateException("1–32 Kameras benötigt.");
                HashSet<String> ids = new HashSet<>();
                for (var camera : loaded) {
                    if (camera.id() == null || !camera.id().matches("[a-zA-Z0-9_-]{1,64}") || !ids.add(camera.id())
                            || camera.name() == null || camera.name().isBlank() || camera.name().length() > 120
                            || Protocol.defaultCities().stream().noneMatch(city -> city.id().equals(camera.city_id()))
                            || !List.of("mock", "mp4", "hls").contains(camera.kind()))
                        throw new IllegalStateException("Ungültige Kamerakonfiguration.");
                    if (!"mock".equals(camera.kind())) {
                        URI url = URI.create(camera.url());
                        if (!"https".equals(url.getScheme()) || url.getHost() == null || url.getUserInfo() != null
                                || camera.url().length() > 2048)
                            throw new IllegalStateException("Kameras benötigen autorisierte HTTPS-URLs ohne eingebettete Zugangsdaten.");
                    }
                }
                cameras = List.copyOf(loaded);
            } catch (Exception ex) {
                throw new IllegalStateException("Kamerakonfiguration nicht lesbar oder ungültig.");
            }
        }
    }

    private void validateEndpoint(String value) {
        URI uri = URI.create(value);
        if (uri.getHost() == null || uri.getUserInfo() != null ||
                !("https".equals(uri.getScheme()) || ("http".equals(uri.getScheme()) && isLoopback(uri.getHost()))))
            throw new IllegalStateException("Provider benötigen HTTPS oder einen lokalen HTTP-Endpunkt.");
    }
    private static boolean isLoopback(String host) {
        return "127.0.0.1".equals(host) || "localhost".equalsIgnoreCase(host) || "::1".equals(host);
    }
    public boolean isDemo() { return demo; }
    public void setDemo(boolean value) { demo = value; }
    public boolean isAuthRequired() { return authRequired || !sessionToken.isBlank() || !isLoopback(bind); }
    public void setAuthRequired(boolean value) { authRequired = value; }
    public boolean isSecureCookie() { return secureCookie != null ? secureCookie : !isLoopback(bind); }
    public void setSecureCookie(Boolean value) { secureCookie = value; }
    public String getBind() { return bind; }
    public void setBind(String value) { bind = value; }
    public String getSessionToken() { return sessionToken; }
    public void setSessionToken(String value) { sessionToken = value; }
    public List<String> getAllowedOrigins() { return allowedOrigins; }
    public void setAllowedOrigins(List<String> value) { allowedOrigins = value; }
    public void setAllowedOrigins(String value) {
        allowedOrigins = Arrays.stream(value.split(",")).map(String::trim).filter(s -> !s.isBlank()).toList();
    }
    public String getStaticDir() { return staticDir; }
    public void setStaticDir(String value) { staticDir = value; }
    public String getCamerasFile() { return camerasFile; }
    public void setCamerasFile(String value) { camerasFile = value; }
    public List<Protocol.Camera> getCameras() { return cameras; }
    public int getMaxRooms() { return maxRooms; }
    public void setMaxRooms(int value) { maxRooms = value; }
    public int getMaxClientsPerRoom() { return maxClientsPerRoom; }
    public void setMaxClientsPerRoom(int value) { maxClientsPerRoom = value; }
    public int getMaxOutputFrames() { return maxOutputFrames; }
    public void setMaxOutputFrames(int value) { maxOutputFrames = value; }
    public int getMaxInputChars() { return maxInputChars; }
    public void setMaxInputChars(int value) { maxInputChars = value; }
    public int getMaxSessionMinutes() { return maxSessionMinutes; }
    public void setMaxSessionMinutes(int value) { maxSessionMinutes = value; }
    public String getAnthropicModel() { return anthropicModel; }
    public void setAnthropicModel(String value) { anthropicModel = value; }
    public String getAnthropicUrl() { return anthropicUrl; }
    public void setAnthropicUrl(String value) { anthropicUrl = value; }
    public String getElevenlabsVoiceId() { return elevenlabsVoiceId; }
    public void setElevenlabsVoiceId(String value) { elevenlabsVoiceId = value; }
    public String getElevenlabsModel() { return elevenlabsModel; }
    public void setElevenlabsModel(String value) { elevenlabsModel = value; }
    public String getElevenlabsUrl() { return elevenlabsUrl; }
    public void setElevenlabsUrl(String value) { elevenlabsUrl = value; }
    public String getSttUrl() { return sttUrl; }
    public void setSttUrl(String value) { sttUrl = value; }
    public String getSttApiKey() { return sttApiKey; }
    public void setSttApiKey(String value) { sttApiKey = value; }
    public String getGenerationBackend() { return generationBackend; }
    public void setGenerationBackend(String value) { generationBackend = value == null ? "python" : value.toLowerCase(); }
    public String getBridgeUrl() { return bridgeUrl; }
    public void setBridgeUrl(String value) { bridgeUrl = value; }
    public String getBridgeToken() { return bridgeToken; }
    public void setBridgeToken(String value) { bridgeToken = value == null ? "" : value; }
    public int getVadThreshold() { return vadThreshold; }
    public void setVadThreshold(int value) { vadThreshold = Math.max(0, Math.min(32767, value)); }
    public Duration sessionTtl() { return Duration.ofMinutes(Math.max(5, Math.min(480, maxSessionMinutes))); }
}
