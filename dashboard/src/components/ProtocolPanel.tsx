import { useCallback, useEffect, useState } from "react";
import { api, type LogEntry } from "../command/api";

const KINDS = [
  { id: "ereignisse", label: "Ereignisse" },
  { id: "aktionen", label: "Aktionen" },
  { id: "audit", label: "Audit" },
] as const;
type Kind = (typeof KINDS)[number]["id"];

const TIME_KEYS = ["ts", "zeit", "time", "t", "erstellt", "start"];
const when = (entry: LogEntry) => {
  const key = TIME_KEYS.find((k) => entry[k] != null);
  const raw = key ? entry[key] : null;
  const date = typeof raw === "number" ? new Date(raw > 1e12 ? raw : raw * 1000) : typeof raw === "string" ? new Date(raw) : null;
  return date && !Number.isNaN(date.getTime()) ? date.toLocaleString("de-DE") : "—";
};
const summary = (entry: LogEntry) =>
  Object.entries(entry)
    .filter(([k]) => !TIME_KEYS.includes(k))
    .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`)
    .join(" · ")
    .slice(0, 400);

/** Protokolle der Python-Seite (nur lesend): früher auf der Oberfläche 8765. */
export function ProtocolPanel() {
  const [kind, setKind] = useState<Kind>("ereignisse");
  const [items, setItems] = useState<LogEntry[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async (next: Kind) => {
    setError(null);
    try {
      const data = await api.logs(next);
      setItems(Array.isArray(data?.eintraege) ? data.eintraege : []); // Python liefert neueste zuerst
    } catch (reason) {
      setItems(null);
      setError(reason instanceof Error ? reason.message : "Protokoll nicht erreichbar.");
    }
  }, []);
  useEffect(() => { void load(kind); }, [kind, load]);
  return (
    <section className="panel ops-panel protocol-panel">
      <div className="eyebrow">PROTOKOLLE (NUR LESEND)</div>
      <div className="ops-links" role="tablist" aria-label="Protokoll wählen">
        {KINDS.map((k) => (
          <button key={k.id} type="button" role="tab" aria-selected={kind === k.id} className={`btn ${kind === k.id ? "amber" : ""}`}
            onClick={() => setKind(k.id)}>{k.label}</button>
        ))}
        <button type="button" className="btn" onClick={() => void load(kind)}>Neu laden</button>
      </div>
      {error && <div className="empty">{error}</div>}
      <div className="scroll-list logs">
        {items?.length === 0 && <div className="empty">Keine Einträge.</div>}
        {items?.map((entry, index) => <div key={index}>{when(entry)} · {summary(entry)}</div>)}
      </div>
    </section>
  );
}
