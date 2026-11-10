/** Gespeicherte Orte, Ortssuche und HUD-Formatierung (reine Funktionen, testbar). */

export type Place = {
  id: string;
  name: string;
  lat: number;
  lon: number;
  /** Zielansicht nach der Landung (Detailstufe mit Satellitenbild). */
  zoom: number;
  pitch: number;
  bearing: number;
  aliases: string[];
  hinweis?: string;
};

export const PLACES: Place[] = [
  { id: "wall-street", name: "Wall Street", lat: 40.70614, lon: -74.00883, zoom: 17.2, pitch: 62, bearing: -28,
    aliases: ["wall street", "new york stock exchange", "nyse", "börse new york", "boerse new york"], hinweis: "New York · Finanzdistrikt" },
  { id: "london", name: "London · City", lat: 51.51355, lon: -0.08837, zoom: 16.4, pitch: 60, bearing: 20,
    aliases: ["london", "city of london", "london city", "londoner city"], hinweis: "Finanzzentrum · Bank of England" },
  { id: "frankfurt", name: "Frankfurt · Börse", lat: 50.11532, lon: 8.67889, zoom: 17, pitch: 58, bearing: -12,
    aliases: ["frankfurt", "frankfurter börse", "frankfurter boerse", "börse frankfurt", "boerse frankfurt"], hinweis: "Deutsche Börse" },
  { id: "berlin", name: "Berlin · Mitte", lat: 52.51627, lon: 13.37770, zoom: 16.2, pitch: 55, bearing: 15,
    aliases: ["berlin", "brandenburger tor", "berlin mitte"] },
  { id: "stuttgart", name: "Stuttgart", lat: 48.77846, lon: 9.17997, zoom: 16, pitch: 55, bearing: 0,
    aliases: ["stuttgart", "schlossplatz"] },
  { id: "silicon-valley", name: "Silicon Valley", lat: 37.38605, lon: -122.08385, zoom: 14.5, pitch: 55, bearing: 30,
    aliases: ["silicon valley", "palo alto", "mountain view"] },
  { id: "tokyo", name: "Tokio · Shibuya", lat: 35.65952, lon: 139.70060, zoom: 17, pitch: 60, bearing: -20,
    aliases: ["tokio", "tokyo", "shibuya"] },
  { id: "dubai", name: "Dubai · Downtown", lat: 25.19720, lon: 55.27437, zoom: 15.8, pitch: 62, bearing: 35,
    aliases: ["dubai", "burj khalifa"] },
  { id: "singapore", name: "Singapur · Marina Bay", lat: 1.28367, lon: 103.86072, zoom: 16, pitch: 58, bearing: -15,
    aliases: ["singapur", "singapore", "marina bay"] },
];

const norm = (s: string) => s.toLowerCase().normalize("NFC").replace(/\s+/g, " ").trim();

/** Bekannter Ort aus einer Anfrage ("Wall Street", "zeig mir London") – sonst undefined (dann Ortssuche). */
export function findPlace(query?: string): Place | undefined {
  if (!query) return undefined;
  const q = norm(query);
  let best: { place: Place; len: number } | undefined;
  for (const place of PLACES)
    for (const alias of [place.id.replace(/-/g, " "), ...place.aliases])
      if (q.includes(alias) && (!best || alias.length > best.len)) best = { place, len: alias.length };
  return best?.place;
}

/** Ergebnis der Ortssuche (Nominatim) als Ziel; Zoom grob nach Ortstyp. */
export function placeFromSearch(hit: { lat: string; lon: string; display_name: string; type?: string; addresstype?: string }): Place {
  const kind = hit.addresstype || hit.type || "";
  const zoom = /country/.test(kind) ? 5.5 : /state|region/.test(kind) ? 7.5 : /city|town|municipality/.test(kind) ? 13.5
    : /suburb|quarter|neighbourhood/.test(kind) ? 15 : 17;
  return {
    id: `suche-${Number(hit.lat).toFixed(4)},${Number(hit.lon).toFixed(4)}`,
    name: hit.display_name.split(",")[0].trim(), lat: Number(hit.lat), lon: Number(hit.lon),
    zoom, pitch: zoom >= 14 ? 58 : 30, bearing: 0, aliases: [], hinweis: hit.display_name.split(",").slice(1, 3).join(",").trim(),
  };
}

export function formatCoord(value: number, axis: "lat" | "lon"): string {
  const hemi = axis === "lat" ? (value >= 0 ? "N" : "S") : value >= 0 ? "E" : "W";
  const abs = Math.abs(value);
  const deg = Math.floor(abs);
  const minFloat = (abs - deg) * 60;
  const min = Math.floor(minFloat);
  const sec = (minFloat - min) * 60;
  return `${String(deg).padStart(axis === "lat" ? 2 : 3, "0")}°${String(min).padStart(2, "0")}′${sec.toFixed(2).padStart(5, "0")}″ ${hemi}`;
}

/** Ungefähre Kamerahöhe aus Zoom und Breite (Web-Mercator, 512-px-Kacheln, ~60° Sichtfeld). */
export function altitudeMeters(zoom: number, lat: number, viewportHeightPx = 900): number {
  const metersPerPixel = (40075016.686 * Math.cos((lat * Math.PI) / 180)) / (512 * 2 ** zoom);
  return Math.max(1, (metersPerPixel * viewportHeightPx) / 2 / Math.tan(Math.PI / 6) / 1.0);
}

export function formatAltitude(m: number): string {
  if (m >= 100_000) return `${Math.round(m / 1000).toLocaleString("de-DE")} km`;
  if (m >= 1000) return `${(m / 1000).toFixed(1).replace(".", ",")} km`;
  return `${Math.round(m)} m`;
}
