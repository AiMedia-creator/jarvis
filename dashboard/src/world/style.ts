/** "Cyber-Map": dunkle Vektorkarte (OpenFreeMap/OpenMapTiles) + Satellitenbild (Esri) für die Detailstufe.

Die Basiskarte wird zur Laufzeit geladen und umgefärbt (Jarvis-Farben: Tiefschwarz/Petrol, Akzent Cyan, Amber nur
für Jarvis-Markierungen). Das Satellitenbild blendet ab Zoom 11 weich ein und ist ab Zoom 14 voll sichtbar;
Beschriftungen bleiben darüber. Keine Schlüssel, keine Konten. Quellenangaben sind Pflicht und werden angezeigt.
*/
import type { StyleSpecification, LayerSpecification } from "maplibre-gl";

export const BASE_STYLE_URL = "https://tiles.openfreemap.org/styles/dark";
export const SATELLITE_TILES = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
export const ATTRIBUTION_SATELLITE =
  'Satellit: <a href="https://www.esri.com" target="_blank" rel="noopener">Esri</a>, Maxar, Earthstar Geographics (Archivbild, Aufnahmedatum unbekannt)';
export const COLORS = { void: "#0f1d2e", land: "#13253a", water: "#0d1b2b", line: "#C9DBEE", text: "#E9F1F9", amber: "#3F78B5" };

export type MapMode = "auto" | "satellit" | "strasse";

export function satelliteOpacity(mode: MapMode): any {
  if (mode === "satellit") return 1;
  if (mode === "strasse") return 0;
  return ["interpolate", ["linear"], ["zoom"], 10.5, 0, 14, 1];
}

function tint(layer: any): void {
  const id = String(layer.id || "");
  const src = String(layer["source-layer"] || "");
  layer.paint = layer.paint || {};
  if (layer.type === "background") layer.paint["background-color"] = COLORS.void;
  else if (layer.type === "fill" && /water/.test(src)) layer.paint["fill-color"] = COLORS.water;
  else if (layer.type === "fill" && /landcover|landuse|park/.test(src)) {
    layer.paint["fill-color"] = COLORS.land;
    layer.paint["fill-opacity"] = 0.6;
  } else if (layer.type === "line" && src === "transportation") {
    layer.paint["line-color"] = COLORS.line;
    layer.paint["line-opacity"] = ["interpolate", ["linear"], ["zoom"], 5, 0.08, 12, 0.22, 16, 0.35];
  } else if (layer.type === "line" && src === "boundary") {
    layer.paint["line-color"] = COLORS.line;
    layer.paint["line-opacity"] = 0.35;
  } else if (layer.type === "symbol") {
    layer.paint["text-color"] = /place|country|city/.test(id) ? "#E6EEF8" : COLORS.text;
    layer.paint["text-halo-color"] = "rgba(0,0,0,0.85)";
    layer.paint["text-halo-width"] = 1.4;
  }
  if (layer.type === "raster" && /ne2|shaded/.test(String(layer.source))) {
    layer.paint["raster-opacity"] = 0.25;  // Relief der Natural-Earth-Karte nur angedeutet
    layer.paint["raster-saturation"] = -1;
  }
}

/** Basisstil -> Jarvis-Stil (pure Funktion; ändert die Eingabe nicht). */
export function buildStyle(base: StyleSpecification, mode: MapMode = "auto"): StyleSpecification {
  const style = JSON.parse(JSON.stringify(base)) as StyleSpecification;
  // Gebäude als halbtransparente 3D-Körper (echte Höhe), damit sie das Satellitenbild nicht zudecken.
  style.layers = style.layers.map((layer: any) =>
    layer.type === "fill" && layer["source-layer"] === "building"
      ? { id: layer.id, type: "fill-extrusion", source: layer.source, "source-layer": "building", minzoom: 14,
          paint: { "fill-extrusion-color": "#0e3440", "fill-extrusion-height": ["coalesce", ["get", "render_height"], 10],
                   "fill-extrusion-base": ["coalesce", ["get", "render_min_height"], 0],
                   "fill-extrusion-opacity": 0.38 } }
      : layer);
  style.layers.forEach(tint);
  style.sources.satellit = {
    type: "raster", tiles: [SATELLITE_TILES], tileSize: 256, maxzoom: 19, attribution: ATTRIBUTION_SATELLITE,
  };
  // Satellit direkt über den Grundflächen (Wasser, Landnutzung), unter Straßen, Gebäuden und Beschriftungen.
  const above = (l: any) => !(["background", "fill", "raster"].includes(l.type) && /^(water|landcover|landuse|park)$|^$/.test(String(l["source-layer"] || "")));
  const firstSymbol = style.layers.findIndex(above);
  const satellite: LayerSpecification = {
    id: "jarvis-satellit", type: "raster", source: "satellit", minzoom: 9,
    paint: { "raster-opacity": satelliteOpacity(mode), "raster-saturation": -0.25, "raster-contrast": 0.08,
             "raster-brightness-max": 0.88, "raster-fade-duration": 300 },
  };
  style.layers.splice(firstSymbol < 0 ? style.layers.length : firstSymbol, 0, satellite);
  (style as any).projection = { type: "globe" };
  (style as any).sky = {
    "sky-color": "#02060c", "horizon-color": "#0b2a3a", "fog-color": "#03070d",
    "atmosphere-blend": ["interpolate", ["linear"], ["zoom"], 0, 1, 5, 1, 7, 0],
  };
  return style;
}
