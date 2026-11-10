import { useCallback, useEffect, useRef, useState } from "react";
import { WorldMap, type Camera } from "../world/WorldMap";
import { PLACES, altitudeMeters, findPlace, formatAltitude, formatCoord, placeFromSearch, type Place } from "../world/places";
import type { MapMode } from "../world/style";
import "../world/world.css";

type Status = "laden" | "bereit" | "offline";

async function geocode(query: string): Promise<Place> {
  const response = await fetch(`/api/geocode?q=${encodeURIComponent(query)}`);
  const body = await response.json().catch(() => null);
  if (!response.ok || body?.status !== "ok") throw new Error(body?.hinweis || body?.fehler || `Suche fehlgeschlagen (${response.status})`);
  return placeFromSearch(body.treffer);
}

/** Jarvis World-View: Globus -> kinoreifer Flug -> Satelliten-Detailansicht, mit HUD. */
export default function WorldView({ query, onBack }: { query?: string; onBack: () => void }) {
  const elementRef = useRef<HTMLDivElement>(null);
  const worldRef = useRef<WorldMap | null>(null);
  const [camera, setCamera] = useState<Camera>({ lat: 25, lon: 15, zoom: 1.55, bearing: 0, pitch: 0 });
  const [status, setStatus] = useState<Status>("laden");
  const [target, setTarget] = useState<Place | null>(null);
  const [mode, setMode] = useState<MapMode>("auto");
  const [search, setSearch] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flying, setFlying] = useState(false);

  useEffect(() => {
    if (!elementRef.current) return;
    const world = new WorldMap(elementRef.current, setCamera);
    worldRef.current = world;
    void world.ready.then((state) => setStatus(state === "ok" ? "bereit" : "offline"));
    return () => {
      world.destroy();
      worldRef.current = null;
    };
  }, []);

  const fly = useCallback(async (place: Place) => {
    setTarget(place);
    setError(null);
    setFlying(true);
    try {
      await worldRef.current?.flyToPlace(place);
    } finally {
      setFlying(false);
    }
  }, []);

  const resolve = useCallback(async (text: string) => {
    const known = findPlace(text);
    if (known) return fly(known);
    setBusy(true);
    try {
      await fly(await geocode(text));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Ort nicht gefunden.");
    } finally {
      setBusy(false);
    }
  }, [fly]);

  // Sprachbefehl / Aufruf mit Ort ("Zeig mir Wall Street auf der Karte")
  useEffect(() => {
    if (query && status !== "laden") void resolve(query);
  }, [query, status, resolve]);

  useEffect(() => worldRef.current?.setMode(mode), [mode]);

  const altitude = altitudeMeters(camera.zoom, camera.lat, elementRef.current?.clientHeight || 900);
  const detail = camera.zoom >= 14 ? (mode === "strasse" ? "STRASSE" : "SATELLIT") : camera.zoom >= 10.5 && mode !== "strasse" ? "ÜBERGANG" : "GLOBUS";
  return (
    <section className="wv" aria-label="Weltansicht">
      <div className="wv-map" ref={elementRef} />
      <div className="wv-vignette" aria-hidden="true" />
      <div className="wv-scan" aria-hidden="true" />
      <div className={`wv-reticle ${flying ? "flying" : ""}`} aria-hidden="true"><i /><i /><i /><i /></div>

      <header className="wv-head">
        <div className="wv-eyebrow">JARVIS · WORLD-VIEW</div>
        <h2 className="wv-title">{target ? target.name : "Globale Lage"}</h2>
        <div className="wv-sub">
          {target?.hinweis ?? "Ziel wählen oder sagen: „Flieg nach London“"}
          {flying && <span className="wv-live"> · ANFLUG</span>}
        </div>
      </header>
      <button type="button" className="wv-close" onClick={onBack} aria-label="Zurück zum Gespräch" title="Zurück zum Gespräch">×</button>

      <aside className="wv-panel" aria-label="Ziele und Ebenen">
        <form className="wv-search" onSubmit={(e) => { e.preventDefault(); if (search.trim()) void resolve(search.trim()); }}>
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Ort suchen …" aria-label="Ort suchen" maxLength={120} />
          <button type="submit" disabled={busy || !search.trim()}>{busy ? "…" : "↵"}</button>
        </form>
        {error && <p className="wv-error" role="status">{error}</p>}
        <div className="wv-places">
          {PLACES.map((p) => (
            <button type="button" key={p.id} className={target?.id === p.id ? "active" : ""} onClick={() => void fly(p)}>
              {p.name}
            </button>
          ))}
          <button type="button" onClick={() => { setTarget(null); void worldRef.current?.overview(); }}>Globus</button>
        </div>
        <div className="wv-modes" role="radiogroup" aria-label="Kartenebene">
          {(["auto", "satellit", "strasse"] as MapMode[]).map((m) => (
            <button type="button" role="radio" aria-checked={mode === m} key={m} className={mode === m ? "active" : ""} onClick={() => setMode(m)}>
              {m === "auto" ? "Auto" : m === "satellit" ? "Satellit" : "Straße"}
            </button>
          ))}
        </div>
        <p className="wv-source">
          {status === "offline" ? "Basiskarte offline – nur Satellitenbild." : "Basiskarte: OpenFreeMap · © OpenStreetMap"}
          <br />Satellit: Esri World Imagery · <b>Archivbild</b>, Aufnahmedatum unbekannt – kein Livebild.
        </p>
      </aside>

      <footer className="wv-ticker" aria-label="Koordinaten">
        <span><em>LAT</em> {formatCoord(camera.lat, "lat")}</span>
        <span><em>LON</em> {formatCoord(camera.lon, "lon")}</span>
        <span><em>ALT</em> {formatAltitude(altitude)}</span>
        <span><em>ZOOM</em> {camera.zoom.toFixed(1)}</span>
        <span><em>KURS</em> {((camera.bearing % 360) + 360) % 360 | 0}°</span>
        <span><em>NEIGUNG</em> {camera.pitch.toFixed(0)}°</span>
        <span className="wv-mode"><em>EBENE</em> {detail}</span>
        <span className={`wv-status ${status}`}>{status === "laden" ? "LÄDT" : status === "offline" ? "OFFLINE" : "ONLINE"}</span>
      </footer>
    </section>
  );
}
