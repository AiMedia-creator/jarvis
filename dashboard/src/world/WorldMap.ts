/** Jarvis-Weltkarte auf MapLibre GL JS (Open-Source-Fork von Mapbox GL JS, gleiche Kamera-API, ohne Konto/Schlüssel).

Globus in der Übersicht, kinoreifer Flug (flyTo mit Zoom-Bogen) in die Detailansicht mit Satellitenbild, danach eine
langsame Umrundung, bis der Nutzer selbst die Karte bewegt. Bei "Bewegung reduzieren" springt die Kamera direkt.
*/
import { Map as MapLibreMap, Marker as MapMarker, setWorkerUrl, type GeoJSONSource, type StyleSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
// MapLibre 6 rechnet in einem Modul-Worker; Vite bündelt ihn samt geteiltem Code zu einer Datei.
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";

setWorkerUrl(workerUrl);
import { BASE_STYLE_URL, COLORS, buildStyle, satelliteOpacity, type MapMode } from "./style";
import type { Place } from "./places";

export type Camera = { lat: number; lon: number; zoom: number; bearing: number; pitch: number };
export type Marker = { id: string; name: string; lat: number; lon: number; count: number };
export type ViewState = { city_id?: string; lat: number; lon: number; height: number };

const reducedMotion = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

function fallbackStyle(mode: MapMode): StyleSpecification {
  // Basiskarte nicht erreichbar: nur Satellit + Hintergrund, damit der Flug trotzdem funktioniert.
  return buildStyle({ version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": COLORS.void } }] } as StyleSpecification, mode);
}

export class WorldMap {
  map: MapLibreMap | null = null;
  readonly ready: Promise<"ok" | "offline">;
  private token = 0;
  private destroyed = false;
  private orbiting = false;
  private targetMarker: MapMarker | null = null;
  private pendingMarkers: { markers: Marker[]; focusId?: string } | null = null;
  private frame = 0;

  constructor(element: HTMLElement, private onMove: (camera: Camera) => void = () => undefined, mode: MapMode = "auto") {
    this.ready = this.init(element, mode);
  }

  private async init(element: HTMLElement, mode: MapMode): Promise<"ok" | "offline"> {
    let style: StyleSpecification;
    let status: "ok" | "offline" = "ok";
    try {
      const response = await fetch(BASE_STYLE_URL);
      if (!response.ok) throw new Error(String(response.status));
      style = buildStyle((await response.json()) as StyleSpecification, mode);
    } catch {
      style = fallbackStyle(mode);
      status = "offline";
    }
    if (this.destroyed) return status;
    const map = new MapLibreMap({
      container: element, style, center: [15, 25], zoom: 1.55, minZoom: 0.8, maxZoom: 19.5, maxPitch: 75,
      attributionControl: { compact: true }, renderWorldCopies: false, fadeDuration: 250,
    });
    this.map = map;
    map.on("move", () => {
      if (this.frame) return;
      this.frame = requestAnimationFrame(() => {
        this.frame = 0;
        const c = map.getCenter();
        this.onMove({ lat: c.lat, lon: c.lng, zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() });
      });
    });
    // Nutzer greift ein -> Umrundung und Flug sofort beenden.
    for (const event of ["mousedown", "wheel", "touchstart", "dragstart"] as const)
      map.on(event, () => this.stopMotion());
    await new Promise<void>((resolve) => map.once("load", () => resolve()));
    if (this.destroyed) return status;
    map.addSource("jarvis-markers", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
    map.addLayer({ id: "jarvis-markers-glow", type: "circle", source: "jarvis-markers",
      paint: { "circle-radius": ["+", 10, ["*", 3, ["get", "count"]]], "circle-color": COLORS.amber, "circle-opacity": 0.18,
               "circle-blur": 0.8, "circle-pitch-alignment": "map" } });
    map.addLayer({ id: "jarvis-markers", type: "circle", source: "jarvis-markers",
      paint: { "circle-radius": ["case", ["get", "focus"], 7, 5], "circle-color": COLORS.amber,
               "circle-stroke-color": "#fff3dd", "circle-stroke-width": ["case", ["get", "focus"], 2, 1] } });
    if (map.getStyle().glyphs)
      map.addLayer({ id: "jarvis-markers-label", type: "symbol", source: "jarvis-markers",
        layout: { "text-field": ["get", "label"], "text-font": ["Noto Sans Regular"], "text-size": 12,
                  "text-offset": [0, -1.4], "text-anchor": "bottom" },
        paint: { "text-color": "#f4ede2", "text-halo-color": "#000", "text-halo-width": 1.5 } });
    if (this.pendingMarkers) this.setMarkers(this.pendingMarkers.markers, this.pendingMarkers.focusId);
    return status;
  }

  setMode(mode: MapMode): void {
    if (this.map?.getLayer("jarvis-satellit")) this.map.setPaintProperty("jarvis-satellit", "raster-opacity", satelliteOpacity(mode));
  }

  stopMotion(): void {
    this.token += 1;
    this.orbiting = false;
    this.map?.stop();
  }

  /** Kinoreifer Flug: ggf. erst auf den Globus hinaus, dann im Bogen bis zur Detailstufe, dann langsame Umrundung. */
  async flyToPlace(place: Place): Promise<void> {
    await this.ready;
    const map = this.map;
    if (!map || this.destroyed) return;
    this.stopMotion();
    const token = this.token;
    this.showTarget(place);
    const target = { center: [place.lon, place.lat] as [number, number], zoom: place.zoom, pitch: place.pitch, bearing: place.bearing };
    if (reducedMotion()) {
      map.jumpTo(target);
      return;
    }
    const here = map.getCenter();
    const far = Math.abs(here.lng - place.lon) + Math.abs(here.lat - place.lat) > 20;
    if (map.getZoom() > 4 && far) {
      await this.animate(() => map.easeTo({ zoom: 2.2, pitch: 0, bearing: 0, duration: 1400, essential: true }));
      if (token !== this.token) return;
    }
    // Feste Flugdauer nach Entfernung (maxDuration würde lange Flüge auf 0 setzen = Sprung statt Flug).
    const zoomGap = Math.max(0, place.zoom - map.getZoom());
    const duration = Math.round(Math.min(8500, Math.max(3200, 2200 + zoomGap * 380 + (far ? 1200 : 0))));
    await this.animate(() => map.flyTo({ ...target, curve: 1.75, duration, essential: true }));
    if (token !== this.token) return;
    this.orbit(token, place.bearing);
  }

  private animate(start: () => void): Promise<void> {
    const map = this.map!;
    return new Promise((resolve) => {
      map.once("moveend", () => resolve());
      start();
    });
  }

  private orbit(token: number, bearing: number): void {
    const map = this.map;
    if (!map || reducedMotion()) return;
    this.orbiting = true;
    const step = (from: number) => {
      if (token !== this.token || !this.orbiting || this.destroyed) return;
      map.once("moveend", () => step(from + 30));
      map.easeTo({ bearing: from + 30, duration: 30000, easing: (t) => t, essential: false });
    };
    step(bearing);
  }

  private showTarget(place: Place): void {
    if (!this.map) return;
    this.targetMarker?.remove();
    const el = document.createElement("div");
    el.className = "wv-target";
    el.setAttribute("aria-hidden", "true");
    this.targetMarker = new MapMarker({ element: el, pitchAlignment: "map" }).setLngLat([place.lon, place.lat]).addTo(this.map);
  }

  /** Weltansicht (Globus), optional über einem Längengrad. */
  async overview(lon = 15, lat = 25, duration = 2400): Promise<void> {
    await this.ready;
    if (!this.map || this.destroyed) return;
    this.stopMotion();
    this.targetMarker?.remove();
    this.targetMarker = null;
    const target = { center: [lon, lat] as [number, number], zoom: 1.55, pitch: 0, bearing: 0 };
    if (reducedMotion() || duration < 50) this.map.jumpTo(target);
    else this.map.flyTo({ ...target, duration, essential: true });
  }

  /** Kompatibel zur Nachrichtenansicht: Ort aus einer Meldung anfliegen. */
  flyTo(city: { id: string; name: string; lat: number; lon: number }): void {
    void this.flyToPlace({ ...city, zoom: 11, pitch: 45, bearing: 0, aliases: [] });
  }

  /** Nur echte, zugeordnete Orte; ersetzt alle bisherigen Markierungen. */
  setMarkers(markers: Marker[], focusId?: string): void {
    const source = this.map?.getSource("jarvis-markers") as GeoJSONSource | undefined;
    if (!source) {
      this.pendingMarkers = { markers, focusId };
      return;
    }
    source.setData({
      type: "FeatureCollection",
      features: markers.map((m) => ({
        type: "Feature", geometry: { type: "Point", coordinates: [m.lon, m.lat] },
        properties: { id: m.id, count: m.count, focus: m.id === focusId, label: m.count > 1 ? `${m.name} · ${m.count}` : m.name },
      })),
    });
  }

  destroy(): void {
    this.destroyed = true;
    this.stopMotion();
    if (this.frame) cancelAnimationFrame(this.frame);
    this.targetMarker?.remove();
    this.map?.remove();
    this.map = null;
  }
}
