import { useEffect, useMemo, useRef, useState } from "react";
import { WorldMap, type Marker } from "../world/WorldMap";
import { ViewFrame, relativeTime } from "./ViewFrame";

export type NewsItem = {
  quelle: string;
  titel: string;
  text: string;
  zeit: string | null;
  link: string;
  ort: { name: string; lat: number; lon: number; art: string } | null;
};
type NewsResponse = {
  status: "ok" | "leer" | "fehler";
  meldungen: NewsItem[];
  hinweis?: string;
  fehler?: string[];
  abgerufen?: string;
};

/** Gruppiert Meldungen mit echtem Ort zu Markierungen; Meldungen ohne Ort bleiben global. */
export function markersFrom(items: NewsItem[]): Marker[] {
  const byPlace = new Map<string, Marker>();
  for (const item of items) {
    if (!item.ort) continue;
    // Gleicher Punkt (z. B. "Moskau" und "Russland (Moskau)") = eine Markierung; Stadtname bevorzugt.
    const key = `${item.ort.lat.toFixed(2)},${item.ort.lon.toFixed(2)}`;
    const existing = byPlace.get(key);
    if (existing) {
      existing.count += 1;
      if (item.ort.art === "stadt") existing.name = item.ort.name;
    } else byPlace.set(key, { id: key, name: item.ort.name, lat: item.ort.lat, lon: item.ort.lon, count: 1 });
  }
  return [...byPlace.values()];
}

export function findFocus(markers: Marker[], query?: string): Marker | undefined {
  if (!query) return undefined;
  const q = query.toLowerCase();
  return markers.find((m) => m.name.toLowerCase().startsWith(q) || m.name.toLowerCase().includes(`(${q}`));
}

export default function NewsView({ query, onBack }: { query?: string; onBack: () => void }) {
  const [data, setData] = useState<NewsResponse | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const elementRef = useRef<HTMLDivElement>(null);
  const worldRef = useRef<WorldMap | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/news?rubrik=allgemein", { signal: controller.signal })
      .then(async (response) => {
        const body = (await response.json()) as NewsResponse;
        if (!response.ok && body?.status !== "fehler") throw new Error(`HTTP ${response.status}`);
        setData(body);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setFailed(String(error?.message ?? error));
      });
    return () => controller.abort();
  }, []);

  // Die Erde baut sich auf: Viewer nur solange diese Ansicht offen ist (danach freigegeben).
  useEffect(() => {
    if (!elementRef.current) return;
    const world = new WorldMap(elementRef.current);
    worldRef.current = world;
    void world.overview(15, 25, 0);
    return () => {
      world.destroy();
      worldRef.current = null;
    };
  }, []);

  const markers = useMemo(() => markersFrom(data?.meldungen ?? []), [data]);
  const focus = useMemo(() => findFocus(markers, query), [markers, query]);

  useEffect(() => {
    const world = worldRef.current;
    if (!world || !data) return;
    world.setMarkers(markers, focus?.id);
    if (focus) world.flyTo({ id: focus.id, name: focus.name, lat: focus.lat, lon: focus.lon });
    else if (markers.length) {
      const lon = markers.reduce((sum, m) => sum + m.lon, 0) / markers.length;
      void world.overview(lon, 25);
    }
  }, [data, markers, focus]);

  const atFocus = (item: NewsItem) =>
    !!item.ort && !!focus && `${item.ort.lat.toFixed(2)},${item.ort.lon.toFixed(2)}` === focus.id;
  const items = (data?.meldungen ?? []).filter((item) => !focus || atFocus(item)).slice(0, 6);
  const placed = markers.reduce((sum, m) => sum + m.count, 0);
  return (
    <ViewFrame
      className="news-view"
      eyebrow="Weltlage"
      title={focus ? focus.name : query ? `Nachrichten · ${query}` : "Nachrichten"}
      meta={
        data?.abgerufen
          ? `Abgerufen ${new Date(data.abgerufen).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" })} · ${placed} von ${data.meldungen.length} Meldungen mit Ortsangabe`
          : undefined
      }
      onBack={onBack}
    >
      <div className="world-canvas" ref={elementRef} />
      <div className="world-vignette" aria-hidden="true" />
      <aside className="news-list" aria-live="polite">
        {!data && !failed && <p className="news-state">Meldungen werden geladen …</p>}
        {failed && <p className="news-state warn">Nachrichten nicht abrufbar ({failed}).</p>}
        {data?.status === "fehler" && <p className="news-state warn">{data.hinweis ?? "Nachrichtenquelle meldet einen Fehler."}</p>}
        {data?.status === "leer" && <p className="news-state">Keine aktuellen Meldungen gefunden.</p>}
        {query && !focus && data?.status === "ok" && (
          <p className="news-state">Keine Meldung eindeutig {query} zugeordnet – globale Übersicht.</p>
        )}
        {items.map((item) => (
          <article className="news-item" key={item.link || item.titel}>
            <div className="news-source">
              <span>{item.quelle}</span>
              <span>{relativeTime(item.zeit)}</span>
              {item.ort && <span className="news-place">{item.ort.name}</span>}
            </div>
            {item.link ? (
              <a href={item.link} target="_blank" rel="noopener noreferrer">
                {item.titel}
              </a>
            ) : (
              <p>{item.titel}</p>
            )}
          </article>
        ))}
        {!!data?.fehler?.length && <p className="news-state small">Nicht erreichbar: {data.fehler.join(", ")}</p>}
      </aside>
    </ViewFrame>
  );
}
