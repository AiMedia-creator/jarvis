package de.jarvis.backend.protocol;

import com.fasterxml.jackson.annotation.JsonInclude;
import java.util.List;
import java.util.regex.Pattern;

public final class Protocol {
    private Protocol() {}
    public static final Pattern ID = Pattern.compile("[a-zA-Z0-9_-]{8,64}");
    public static boolean validId(String value) { return value != null && ID.matcher(value).matches(); }
    public static String safe(String value, int max) {
        return value == null ? "" : value.substring(0, Math.min(max, value.length()));
    }
    public record City(String id, String name, String country, double lat, double lon) {}
    @JsonInclude(JsonInclude.Include.NON_NULL)
    public record Camera(String id, String name, String city_id, String kind, String url) {}
    public record Config(boolean demo, boolean auth_required, List<City> cities, List<Camera> cameras) {}
    public record ErrorMessage(String type, String message) {}
    private static final List<City> CITIES = List.of(
            new City("berlin", "Berlin", "Deutschland", 52.5200, 13.4050),
            new City("london", "London", "Großbritannien", 51.5074, -0.1278),
            new City("new-york", "New York", "USA", 40.7128, -74.0060),
            new City("tokyo", "Tokio", "Japan", 35.6762, 139.6503),
            new City("dubai", "Dubai", "VAE", 25.2048, 55.2708),
            new City("singapore", "Singapur", "Singapur", 1.3521, 103.8198),
            new City("sydney", "Sydney", "Australien", -33.8688, 151.2093),
            new City("cape-town", "Kapstadt", "Südafrika", -33.9249, 18.4241));
    private static final List<Camera> CAMERAS = CITIES.stream()
            .map(city -> new Camera(city.id() + "-01", city.name() + " · Sektor 01", city.id(), "mock", null))
            .toList();
    public static List<City> defaultCities() { return CITIES; }
    public static List<Camera> defaultCameras() { return CAMERAS; }
}
