import type { Connection, Connections, ConnectionState } from "./api";

const LABEL: Record<ConnectionState, string> = {
  verbunden: "Verbunden", unvollstaendig: "Unvollständig", abgelaufen: "Abgelaufen", nicht_eingerichtet: "Nicht eingerichtet", fehler: "Fehler",
};
export const connectionClass = (z: ConnectionState) =>
  z === "verbunden" ? "bereit" : z === "nicht_eingerichtet" ? "nicht_eingerichtet" : "fehler";
const ORDER: ConnectionState[] = ["abgelaufen", "unvollstaendig", "fehler", "nicht_eingerichtet", "verbunden"];
const time = (iso?: string) => {
  if (!iso) return "";
  const d = new Date(iso.replace(/([+-]\d{2})(\d{2})$/, "$1:$2"));
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
};

function Card({ c }: { c: Connection }) {
  const services = Object.values(c.dienste);
  return (
    <article className="cc-card-box cn-card">
      <div className="head"><strong>{c.name}</strong><span className={`cc-state ${connectionClass(c.zustand)}`}>{LABEL[c.zustand]}</span></div>
      <p className="cn-purpose">{c.zweck}</p>
      {services.length > 0 && (
        <ul className="cn-services" aria-label="Berechtigungen">
          {services.map((s) => <li key={s.name} className={s.aktiv ? "on" : "off"}>{s.aktiv ? "✓" : "✕"} {s.name}</li>)}
        </ul>
      )}
      {c.hinweis && <p>{c.hinweis}</p>}
      {c.details.length > 0 && <ul className="cn-details">{c.details.map((d, i) => <li key={i}>{d}</li>)}</ul>}
      {c.schritte.length > 0 && (
        <div className="cn-steps"><h4>Das musst du tun</h4><ol>{c.schritte.map((s, i) => <li key={i}>{s}</li>)}</ol></div>
      )}
      <p className="cc-hint">{c.pruefung === "live" ? `Live beim Anbieter geprüft${time(c.geprueft) ? ` um ${time(c.geprueft)}` : ""}.` : "Nur Konfiguration geprüft, nicht der Zugang selbst."}</p>
    </article>
  );
}

/** Verbindungen und Einrichtung: echter Zustand je Dienst, sortiert nach „braucht dich zuerst“. Keine Zugangsdaten. */
export function ConnectionsPanel({ data, error, loading, onRefresh }: { data: Connections | null; error: string | null; loading: boolean; onRefresh: () => void }) {
  if (!data) return <p className="cc-empty">{error ?? "Verbindungen werden geprüft …"}</p>;
  if (data.demo) return <p className="cc-empty">Demo-Modus: Es werden keine Dienste geprüft.</p>;
  const sorted = [...data.verbindungen].sort((a, b) => ORDER.indexOf(a.zustand) - ORDER.indexOf(b.zustand));
  const groups: { title: string; items: Connection[] }[] = [
    { title: "Braucht dich", items: sorted.filter((c) => ["abgelaufen", "unvollstaendig", "fehler"].includes(c.zustand)) },
    { title: "Verbunden", items: sorted.filter((c) => c.zustand === "verbunden") },
    { title: "Noch nicht eingerichtet", items: sorted.filter((c) => c.zustand === "nicht_eingerichtet") },
  ];
  return (
    <div className="cn">
      <div className="cc-actions cn-top">
        <button type="button" className="cc-btn small" disabled={loading} onClick={onRefresh}>{loading ? "Prüft …" : "Jetzt neu prüfen"}</button>
        {error && <span className="ac-err">{error}</span>}
      </div>
      {groups.filter((g) => g.items.length > 0).map((g) => (
        <section key={g.title} aria-label={g.title}>
          <h3 className="cn-group">{g.title} <span>{g.items.length}</span></h3>
          <div className="cc-grid">{g.items.map((c) => <Card key={c.id} c={c} />)}</div>
        </section>
      ))}
      <p className="cc-hint">Schlüssel und Tokens bleiben auf diesem Mac und werden hier nie angezeigt. Trage sie nie in ein Gespräch ein.</p>
    </div>
  );
}
