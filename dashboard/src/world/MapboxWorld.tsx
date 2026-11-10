import { useCallback, useEffect, useRef, useState } from "react";
import type { Map as MapboxMap, Marker as MapboxMarker } from "mapbox-gl";
import { PLACES, findPlace, formatCoord, placeFromSearch, type Place } from "./places";
import type { WorldMap, Camera } from "./WorldMap";
import "./mapbox-world.css";

/** Kartenebenen (Mapbox-Stile). Satellitenbilder sind Mosaike aus Archivaufnahmen – keine Live-Übertragung. */
export const LAYERS = [
  { id: "satellite-streets", label: "Satellit + Straßen", style: "mapbox://styles/mapbox/satellite-streets-v12" },
  { id: "satellite", label: "Satellit", style: "mapbox://styles/mapbox/satellite-v9" },
  { id: "streets", label: "Straßen", style: "mapbox://styles/mapbox/streets-v12" },
  { id: "light", label: "Hell", style: "mapbox://styles/mapbox/light-v11" },
] as const;
type LayerId = (typeof LAYERS)[number]["id"];
const QUICK = ["wall-street", "london", "tokyo", "frankfurt", "berlin", "dubai"];

/** Nur öffentliche Tokens (pk.) zulassen; geheime sk.-Schlüssel gehören nie in den Browser. */
export function mapboxToken(raw: string | undefined = import.meta.env.VITE_MAPBOX_ACCESS_TOKEN): string | null {
  const token = (raw ?? "").trim();
  return /^pk\.[A-Za-z0-9._-]{20,}$/.test(token) ? token : null;
}

const reducedMotion = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

async function geocode(query: string): Promise<Place> {
  const response = await fetch(`/api/geocode?q=${encodeURIComponent(query)}`);
  const body = await response.json().catch(() => null);
  if (!response.ok || body?.status !== "ok") throw new Error(body?.hinweis || body?.fehler || `Suche fehlgeschlagen (${response.status})`);
  return placeFromSearch(body.treffer);
}

type Engine = "mapbox" | "ersatz" | "einrichtung";

export default function MapboxWorld({ query, webcams }: { query?: string; webcams: "bereit" | "nicht_eingerichtet" | null }) {
  const token = mapboxToken();
  const [engine, setEngine] = useState<Engine>(token ? "mapbox" : "einrichtung");
  const element = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MapboxMap | null>(null);
  const fallbackRef = useRef<WorldMap | null>(null);
  const markersRef = useRef<MapboxMarker[]>([]);
  const targetMarker = useRef<MapboxMarker | null>(null);
  const [layer, setLayer] = useState<LayerId>("satellite-streets");
  const [camera, setCamera] = useState<Camera>({ lat: 30, lon: 10, zoom: 1.6, bearing: 0, pitch: 0 });
  const [target, setTarget] = useState<Place | null>(null);
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  // ---- Mapbox (mit Token) ----
  useEffect(() => {
    if (engine !== "mapbox" || !element.current || !token) return;
    let cancelled = false;
    let frame = 0;
    void (async () => {
      const [{ default: mapboxgl }] = await Promise.all([import("mapbox-gl"), import("mapbox-gl/dist/mapbox-gl.css")]);
      if (cancelled || !element.current) return;
      mapboxgl.accessToken = token;
      const map = new mapboxgl.Map({
        container: element.current, style: LAYERS[0].style, center: [10, 30], zoom: 1.6, projection: "globe",
        attributionControl: true, cooperativeGestures: false, language: "de",
      });
      mapRef.current = map;
      map.addControl(new mapboxgl.NavigationControl({ visualizePitch: true }), "top-right");
      map.addControl(new mapboxgl.FullscreenControl(), "top-right");
      map.addControl(new mapboxgl.ScaleControl({ unit: "metric", maxWidth: 120 }), "bottom-left");
      const report = () => {
        cancelAnimationFrame(frame);
        frame = requestAnimationFrame(() => {
          const c = map.getCenter();
          setCamera({ lat: c.lat, lon: c.lng, zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() });
        });
      };
      map.on("move", report);
      map.on("style.load", () => {
        map.setFog({ color: "rgb(233, 241, 249)", "high-color": "rgb(63, 120, 181)", "horizon-blend": 0.04, "space-color": "rgb(23, 43, 67)", "star-intensity": 0 });
      });
      map.on("load", () => { if (!cancelled) setReady(true); report(); });
      map.on("error", (e) => {
        const status = (e as any)?.error?.status;
        if (status === 401 || status === 403) setError("Mapbox lehnt den Token ab (ungültig oder für diese Adresse nicht freigegeben).");
      });
      markersRef.current = PLACES.map((p) => {
        const dot = document.createElement("button");
        dot.type = "button";
        dot.className = "wm-marker";
        dot.title = p.name;
        dot.setAttribute("aria-label", `Zu ${p.name} fliegen`);
        dot.addEventListener("click", () => void fly(p));
        return new mapboxgl.Marker({ element: dot }).setLngLat([p.lon, p.lat]).addTo(map);
      });
    })().catch(() => setError("Mapbox GL konnte nicht geladen werden."));
    return () => {
      cancelled = true;
      cancelAnimationFrame(frame);
      markersRef.current.forEach((m) => m.remove());
      markersRef.current = [];
      targetMarker.current = null;
      mapRef.current?.remove();
      mapRef.current = null;
      setReady(false);
    };
    // fly ist stabil (useCallback ohne Abhängigkeiten von engine)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [engine, token]);

  // ---- Ersatzkarte ohne Token (bestehende MapLibre-Karte, Esri-Satellit, klar gekennzeichnet) ----
  useEffect(() => {
    if (engine !== "ersatz" || !element.current) return;
    let world: WorldMap | null = null;
    let cancelled = false;
    void import("./WorldMap").then(({ WorldMap }) => {
      if (cancelled || !element.current) return;
      world = new WorldMap(element.current, setCamera);
      fallbackRef.current = world;
      void world.ready.then((state) => {
        if (cancelled) return;
        setReady(state === "ok");
        if (state !== "ok") setError("Ersatzkarte nicht erreichbar (offline).");
      });
    });
    return () => {
      cancelled = true;
      world?.destroy();
      fallbackRef.current = null;
      setReady(false);
    };
  }, [engine]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || engine !== "mapbox") return;
    const style = LAYERS.find((l) => l.id === layer)?.style;
    if (style) map.setStyle(style);
  }, [layer, engine]);

  const fly = useCallback(async (place: Place) => {
    setTarget(place);
    setError(null);
    const map = mapRef.current;
    if (map) {
      const options = { center: [place.lon, place.lat] as [number, number], zoom: place.zoom, pitch: place.pitch, bearing: place.bearing };
      if (reducedMotion()) map.jumpTo(options);
      else map.flyTo({ ...options, duration: 5200, curve: 1.42, speed: 0.9, essential: true });
      const { default: mapboxgl } = await import("mapbox-gl");
      targetMarker.current?.remove();
      const pin = document.createElement("div");
      pin.className = "wm-pin";
      pin.setAttribute("aria-hidden", "true");
      targetMarker.current = new mapboxgl.Marker({ element: pin, anchor: "bottom" }).setLngLat([place.lon, place.lat]).addTo(map);
      return;
    }
    await fallbackRef.current?.flyToPlace(place);
  }, []);

  const resolve = useCallback(async (text: string) => {
    const value = text.trim();
    if (!value) return;
    const known = findPlace(value);
    if (known) return fly(known);
    setBusy(true);
    try {
      await fly(await geocode(value));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ort nicht gefunden.");
    } finally {
      setBusy(false);
    }
  }, [fly]);

  // Sprachbefehl/Navigation mit Ort ("Zeig mir Wall Street"): nach dem Laden anfliegen.
  useEffect(() => {
    if (ready && query && engine !== "einrichtung") void resolve(query);
  }, [ready, query, engine, resolve]);

  return (
    <div className="wm">
      <div className="wm-stage">
        <div ref={element} className="wm-map" aria-label="Weltkarte" role="region" />
        {engine === "einrichtung" && (
          <div className="wm-setup">
            <div className="cc-card-box">
              <h3>Mapbox einrichten</h3>
              <p>Für die Satellitenkarte braucht die World View einen öffentlichen Mapbox-Token. Es wird keine Karte vorgetäuscht.</p>
              <ol>
                <li>Token unter <a href="https://account.mapbox.com/access-tokens/" target="_blank" rel="noopener noreferrer">account.mapbox.com</a> erstellen (beginnt mit <span className="cc-mono">pk.</span>).</li>
                <li>In <span className="cc-mono">dashboard/.env.local</span> eintragen: <span className="cc-mono">VITE_MAPBOX_ACCESS_TOKEN=pk.…</span> (Vorlage: <span className="cc-mono">.env.example</span>).</li>
                <li>Entwicklungsserver neu starten bzw. Dashboard neu bauen.</li>
              </ol>
              <button type="button" className="cc-btn" onClick={() => setEngine("ersatz")}>Ersatzkarte ohne Mapbox anzeigen</button>
              <p className="cc-hint">Ersatzkarte: OpenFreeMap + Esri World Imagery (Archivbilder), deutlich als Ersatz gekennzeichnet.</p>
            </div>
          </div>
        )}
        {engine !== "einrichtung" && (
          <div className="wm-ticker" aria-live="off">
            <span>{formatCoord(camera.lat, "lat")}</span>
            <span>{formatCoord(camera.lon, "lon")}</span>
            <span>Zoom {camera.zoom.toFixed(1)}</span>
            <span>Neigung {Math.round(camera.pitch)}°</span>
            <span>Richtung {Math.round(((camera.bearing % 360) + 360) % 360)}°</span>
          </div>
        )}
        {engine === "ersatz" && <div className="wm-badge">Ersatzkarte · ohne Mapbox</div>}
      </div>
      <aside className="wm-panel" aria-label="Kartensteuerung">
        <form onSubmit={(e) => { e.preventDefault(); void resolve(search); }} className="wm-search">
          <input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Ort suchen, z. B. Wall Street"
            aria-label="Ort suchen" disabled={engine === "einrichtung"} />
          <button type="submit" className="cc-btn accent" disabled={busy || engine === "einrichtung" || !search.trim()}>{busy ? "Sucht …" : "Fliegen"}</button>
        </form>
        <div>
          <h3>Orte</h3>
          <div className="wm-chips">
            {QUICK.map((id) => PLACES.find((p) => p.id === id)!).map((p) => (
              <button key={p.id} type="button" className={`cc-btn small ${target?.id === p.id ? "accent" : ""}`} disabled={engine === "einrichtung"}
                onClick={() => void fly(p)}>{p.name}</button>
            ))}
          </div>
        </div>
        {engine === "mapbox" && (
          <div>
            <h3>Kartenebene</h3>
            <div className="cc-tabs wm-layers" role="group" aria-label="Kartenebene">
              {LAYERS.map((l) => (
                <button key={l.id} type="button" aria-pressed={layer === l.id} onClick={() => setLayer(l.id)}>{l.label}</button>
              ))}
            </div>
          </div>
        )}
        {target && (
          <div className="cc-detail">
            <h3>{target.name}</h3>
            {target.hinweis && <p style={{ margin: 0, color: "var(--cc-muted)" }}>{target.hinweis}</p>}
            <p className="cc-mono" style={{ margin: 0 }}>{target.lat.toFixed(5)}, {target.lon.toFixed(5)}</p>
          </div>
        )}
        {error && <div className="cc-error" style={{ margin: 0 }} role="alert">{error}</div>}
        <div>
          <h3>Datenstatus</h3>
          <ul className="cc-list">
            <li className="cc-row"><span className="grow"><span className="title">Satellitenbilder</span>
              <span className="meta">Mosaik aus Archivaufnahmen – keine Echtzeit; Aufnahmedatum je Kachel unterschiedlich.</span></span>
              <span className={`cc-state ${engine === "mapbox" ? "bereit" : "nicht_eingerichtet"}`}>{engine === "mapbox" ? "Mapbox" : engine === "ersatz" ? "Esri" : "Token fehlt"}</span></li>
            <li className="cc-row"><span className="grow"><span className="title">Straßenansicht (Bodenhöhe)</span>
              <span className="meta">Braucht einen lizenzierten Bildanbieter mit eigenem Schlüssel.</span></span>
              <span className="cc-state nicht_eingerichtet">Nicht eingerichtet</span></li>
            <li className="cc-row"><span className="grow"><span className="title">Öffentliche Webcams</span>
              <span className="meta">Über Jarvis im Gespräch: „Zeig mir Berlin live“ (YouTube-Livestreams, Windy).</span></span>
              <span className={`cc-state ${webcams ?? "nicht_eingerichtet"}`}>{webcams === "bereit" ? "Windy bereit" : webcams ? "Nur YouTube" : "–"}</span></li>
          </ul>
        </div>
      </aside>
    </div>
  );
}
