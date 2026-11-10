import { useEffect, useMemo, useState } from "react";
import { ViewFrame } from "./ViewFrame";

type Point = { t: string; c: number };
type Security = { symbol: string; name: string; currency: string | null; exchange: string | null };
export type MarketResponse =
  | { status: "ok"; security: Security; quelle: string; stand: string; demo: boolean; punkte: Point[]; zeitraum: { von: string; bis: string } }
  | { status: "mehrdeutig"; optionen: { symbol: string; name: string }[] }
  | { status: "nicht_eingerichtet" | "fehler"; security?: Security; hinweis: string; quelle?: string }
  | { status: "unbekannt" };

export const RANGES = [
  { id: "1M", label: "1 Monat", days: 31 },
  { id: "3M", label: "3 Monate", days: 92 },
  { id: "MAX", label: "Verfügbar", days: Infinity },
] as const;
export type RangeId = (typeof RANGES)[number]["id"];

/** Ausschnitt aus den gelieferten Punkten – rein filternd, Werte bleiben unverändert. */
export function slice(points: Point[], range: RangeId): Point[] {
  const spec = RANGES.find((r) => r.id === range)!;
  if (!points.length || spec.days === Infinity) return points;
  const last = new Date(points[points.length - 1].t).getTime();
  const from = last - spec.days * 86400000;
  return points.filter((p) => new Date(p.t).getTime() >= from);
}

export function covers(points: Point[], range: RangeId): boolean {
  const spec = RANGES.find((r) => r.id === range)!;
  if (spec.days === Infinity || points.length < 2) return points.length >= 2;
  const span = (new Date(points[points.length - 1].t).getTime() - new Date(points[0].t).getTime()) / 86400000;
  return span >= spec.days * 0.85;
}

const money = (value: number, currency: string | null) =>
  currency
    ? value.toLocaleString("de-DE", { style: "currency", currency, maximumFractionDigits: 2 })
    : value.toLocaleString("de-DE", { maximumFractionDigits: 2 });
const day = (iso: string) => new Date(iso).toLocaleDateString("de-DE", { day: "2-digit", month: "short", year: "2-digit" });

function Chart({ points, currency }: { points: Point[]; currency: string | null }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 1000;
  const H = 420;
  const pad = { l: 8, r: 8, t: 20, b: 28 };
  const values = points.map((p) => p.c);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (i: number) => pad.l + (i / Math.max(1, points.length - 1)) * (W - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - (v - min) / span) * (H - pad.t - pad.b);
  const line = points.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.c).toFixed(1)}`).join("");
  const area = `${line}L${x(points.length - 1)},${H - pad.b}L${x(0)},${H - pad.b}Z`;
  const up = values[values.length - 1] >= values[0];
  const active = hover ?? points.length - 1;
  return (
    <figure className="chart">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`Kursverlauf von ${day(points[0].t)} bis ${day(points[points.length - 1].t)}, zwischen ${money(min, currency)} und ${money(max, currency)}`}
        onPointerMove={(event) => {
          const rect = (event.currentTarget as SVGSVGElement).getBoundingClientRect();
          const ratio = (event.clientX - rect.left) / rect.width;
          setHover(Math.max(0, Math.min(points.length - 1, Math.round(ratio * (points.length - 1)))));
        }}
        onPointerLeave={() => setHover(null)}
      >
        <defs>
          <linearGradient id="chart-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#ff9500" stopOpacity="0.32" />
            <stop offset="100%" stopColor="#ff9500" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[0.25, 0.5, 0.75].map((f) => (
          <line key={f} className="chart-grid" x1={pad.l} x2={W - pad.r} y1={pad.t + f * (H - pad.t - pad.b)} y2={pad.t + f * (H - pad.t - pad.b)} />
        ))}
        <path className="chart-area" d={area} fill="url(#chart-fill)" />
        <path className={`chart-line ${up ? "up" : "down"}`} d={line} pathLength={1} />
        <line className="chart-cursor" x1={x(active)} x2={x(active)} y1={pad.t} y2={H - pad.b} />
        <circle className="chart-dot" cx={x(active)} cy={y(points[active].c)} r={6} />
      </svg>
      <figcaption className="chart-readout">
        <strong>{money(points[active].c, currency)}</strong>
        <span>{day(points[active].t)} · Schlusskurs</span>
      </figcaption>
      <div className="chart-scale">
        <span>{day(points[0].t)}</span>
        <span>
          Tief {money(min, currency)} · Hoch {money(max, currency)}
        </span>
        <span>{day(points[points.length - 1].t)}</span>
      </div>
    </figure>
  );
}

export default function MarketView({ query, onBack, onPick }: { query?: string; onBack: () => void; onPick: (symbol: string) => void }) {
  const [data, setData] = useState<MarketResponse | null>(null);
  const [failed, setFailed] = useState<string | null>(null);
  const [range, setRange] = useState<RangeId>("3M");
  const [typed, setTyped] = useState("");

  useEffect(() => {
    setData(null);
    setFailed(null);
    if (!query) return;
    const controller = new AbortController();
    fetch(`/api/market?q=${encodeURIComponent(query)}`, { signal: controller.signal })
      .then(async (response) => setData((await response.json()) as MarketResponse))
      .catch((error) => {
        if (!controller.signal.aborted) setFailed(String(error?.message ?? error));
      });
    return () => controller.abort();
  }, [query]);

  const ok = data?.status === "ok" ? data : null;
  // Liefert die Quelle den gewählten Zeitraum nicht, wird der verfügbare gezeigt (nie aufgefüllt).
  const effective: RangeId = ok && !covers(ok.punkte, range) ? "MAX" : range;
  const shown = useMemo(() => (ok ? slice(ok.punkte, effective) : []), [ok, effective]);
  const change = shown.length > 1 ? (shown[shown.length - 1].c / shown[0].c - 1) * 100 : null;
  const security = ok?.security ?? (data && "security" in data ? data.security : undefined);

  return (
    <ViewFrame
      className="market-view"
      eyebrow="Märkte"
      title={
        security ? (
          <>
            {security.name} <span className="view-symbol">{security.symbol}</span>
          </>
        ) : (
          "Kursverlauf"
        )
      }
      meta={
        ok ? (
          <>
            {[security?.exchange, security?.currency].filter(Boolean).join(" · ")} · Quelle: {ok.quelle} · Stand {ok.stand}
            {ok.demo && <span className="demo-badge">DEMO-DATEN</span>}
          </>
        ) : undefined
      }
      onBack={onBack}
    >
      {!query && (
        <form
          className="market-ask"
          onSubmit={(event) => {
            event.preventDefault();
            if (typed.trim()) onPick(typed.trim());
          }}
        >
          <label htmlFor="market-query">Welches Wertpapier?</label>
          <input id="market-query" value={typed} maxLength={60} onChange={(e) => setTyped(e.target.value)} placeholder="z. B. Apple oder AAPL" />
          <button type="submit">Anzeigen</button>
        </form>
      )}
      {query && !data && !failed && <p className="market-state">Kurse werden geladen …</p>}
      {failed && <p className="market-state warn">Kursdaten nicht abrufbar ({failed}).</p>}
      {data?.status === "unbekannt" && <p className="market-state">„{query}“ ist kein bekanntes Wertpapier. Bitte Namen oder Tickersymbol nennen.</p>}
      {data?.status === "mehrdeutig" && (
        <div className="market-state">
          <p>„{query}“ ist mehrdeutig. Welches Wertpapier meinst du?</p>
          <div className="market-options">
            {data.optionen.map((option) => (
              <button type="button" key={option.symbol} onClick={() => onPick(option.symbol)}>
                {option.name} <span>{option.symbol}</span>
              </button>
            ))}
          </div>
        </div>
      )}
      {(data?.status === "nicht_eingerichtet" || data?.status === "fehler") && (
        <p className="market-state warn">
          {data.status === "nicht_eingerichtet" ? "Datenquelle nicht eingerichtet. " : ""}
          {data.hinweis}
        </p>
      )}
      {ok && shown.length > 1 && (
        <>
          <div className="market-summary">
            {change !== null && (
              <span className={change >= 0 ? "up" : "down"}>
                {change >= 0 ? "+" : ""}
                {change.toLocaleString("de-DE", { maximumFractionDigits: 2 })} %
              </span>
            )}
            <div className="range-switch" role="group" aria-label="Zeitraum">
              {RANGES.map((r) => (
                <button
                  type="button"
                  key={r.id}
                  className={effective === r.id ? "active" : ""}
                  aria-pressed={effective === r.id}
                  disabled={!covers(ok.punkte, r.id)}
                  title={covers(ok.punkte, r.id) ? r.label : "Von der Datenquelle nicht geliefert"}
                  onClick={() => setRange(r.id)}
                >
                  {r.id === "MAX" ? `${r.label} (${day(ok.zeitraum.von)} – ${day(ok.zeitraum.bis)})` : r.label}
                </button>
              ))}
            </div>
          </div>
          <Chart key={`${security?.symbol}-${effective}`} points={shown} currency={security?.currency ?? null} />
        </>
      )}
    </ViewFrame>
  );
}
