import type { Automations, Job } from "./api";

const STATE_LABEL: Record<string, string> = {
  bereit: "Bereit", nicht_eingerichtet: "Nicht eingerichtet", fehler: "Fehler",
  SUCCEEDED: "Erfolgreich", FAILED: "Fehlgeschlagen", UNCLEAR: "Unklar", STARTED: "Läuft", PLANNED: "Geplant",
  RUNNING: "Läuft", COMPLETED: "Erledigt", CANCELLED: "Abgebrochen", WAITING_FOR_APPROVAL: "Wartet auf Freigabe",
};
export const stateLabel = (s: string) => STATE_LABEL[s] ?? s;

const when = (ts: number | null | undefined) =>
  ts ? new Date(ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "–";

function Unavailable({ error }: { error: string | null }) {
  return <p className="cc-empty">{error ?? "Lädt …"}</p>;
}

/** Agenten: was Jarvis an KI-Rollen tatsächlich bereitstellt – mit echtem Einrichtungsstatus. */
export function AgentsPanel({ data, jobs, error, canAsk, onAsk }: {
  data: Automations | null; jobs: Job[]; error: string | null; canAsk: boolean; onAsk: (text: string) => void;
}) {
  if (!data) return <Unavailable error={error} />;
  const running = jobs.filter((j) => j.status === "RUNNING" || j.status === "WAITING_FOR_APPROVAL");
  return (
    <div className="cc-grid">
      {data.agenten.map((a) => (
        <article key={a.id} className="cc-card-box cc-agent">
          <div className="head">
            <strong>{a.name}</strong>
            <span className={`cc-state ${a.status}`}>{stateLabel(a.status)}</span>
          </div>
          <p>{a.rolle}</p>
          <div><span className="cc-mono">{a.modell}</span></div>
          {a.hinweis && <p>{a.hinweis}</p>}
          {a.id === "gremium" && canAsk && a.status === "bereit" && (
            <div><button type="button" className="cc-btn small"
              onClick={() => onAsk("Hol dir das Fachgremium dazu: Was ist diese Woche mein größter Hebel für Umsatz?")}>
              Gremium im Gespräch fragen</button></div>
          )}
        </article>
      ))}
      <article className="cc-card-box">
        <header><h3>Laufende Arbeit</h3><span className="cc-tag">{running.length} aktiv</span></header>
        {jobs.length === 0 ? <p className="cc-empty">Noch keine Jobs seit dem letzten Start.</p> : (
          <ul className="cc-list">
            {jobs.slice(0, 8).map((j) => (
              <li key={j.id} className="cc-row">
                <span className="grow"><span className="title">{j.titel || "Auftrag"}</span>
                  <span className="meta">{j.art} · {when(j.start)} · {j.schritte.length} Schritte</span></span>
                <span className={`cc-state ${j.status}`}>{stateLabel(j.status)}</span>
              </li>
            ))}
          </ul>
        )}
      </article>
    </div>
  );
}

/** Automatisierungen: geplante Abläufe, n8n-Workflows und Integrationen – nur echter Status. */
export function AutomationsPanel({ data, error }: { data: Automations | null; error: string | null }) {
  if (!data) return <Unavailable error={error} />;
  return (
    <div className="cc-grid two">
      <article className="cc-card-box" style={{ gridColumn: "1 / -1" }}>
        <header>
          <h3>Geplante Abläufe</h3>
          <span className={`cc-state ${data.ablaeufe_schleife === "läuft" ? "bereit" : "aus"}`}>
            Zeitplaner {data.ablaeufe_schleife === "läuft" ? "läuft" : "aus"}
          </span>
        </header>
        <div style={{ overflowX: "auto" }}>
          <table className="cc-table">
            <thead><tr><th>Ablauf</th><th>Art</th><th>Status</th><th>Letzter Lauf</th></tr></thead>
            <tbody>
              {data.ablaeufe.map((w) => (
                <tr key={w.id}>
                  <td><strong>{w.name}</strong><p>{w.beschreibung}</p></td>
                  <td>{w.art}</td>
                  <td><span className={`cc-state ${w.aktiv ? "an" : "aus"}`}>{w.aktiv ? "Aktiv" : "Aus"}</span></td>
                  <td>{w.letzter_lauf ? (
                    <><span className={`cc-state ${w.letzter_lauf.status}`}>{stateLabel(w.letzter_lauf.status)}</span>
                      <p>{when(w.letzter_lauf.zeit)}{w.letzter_lauf.fehler ? ` · ${w.letzter_lauf.fehler}` : ""}</p></>
                  ) : <span className="cc-empty">noch nie</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="cc-hint">Ein- und ausschalten in <span className="cc-mono">~/.jarvis/ablaeufe.json</span>. Abläufe arbeiten nur
          mit festem Werkzeug-Rahmen und können nie selbst freigeben.
          {data.ablaeufe_schleife !== "läuft" && " Der Zeitplaner läuft in diesem Prozess gerade nicht – geplante Läufe starten erst, wenn er aktiv ist."}</p>
      </article>
      <article className="cc-card-box">
        <header><h3>n8n-Workflows</h3><span className={`cc-state ${data.n8n.status}`}>{stateLabel(data.n8n.status)}</span></header>
        {data.n8n.workflows.length === 0 ? (
          <p className="cc-empty">{data.n8n.hinweis}. Workflows werden in <span className="cc-mono">~/.jarvis/n8n.json</span> eingetragen;
            Jarvis kann sie dann starten (mit Freigabe, außer ausdrücklich freigabefrei).</p>
        ) : (
          <ul className="cc-list">
            {data.n8n.workflows.map((w) => (
              <li key={w.name} className="cc-row"><span className="grow"><span className="title">{w.name}</span>
                <span className="meta">{w.beschreibung || "–"}</span></span>
                <span className="cc-tag">{w.freigabe ? "mit Freigabe" : "ohne Freigabe"}</span></li>
            ))}
          </ul>
        )}
      </article>
      <article className="cc-card-box">
        <header><h3>Integrationen</h3>
          <span className="cc-tag">{data.integrationen.filter((i) => i.status === "bereit").length} von {data.integrationen.length} bereit</span></header>
        <ul className="cc-list">
          {data.integrationen.map((i) => (
            <li key={i.id} className="cc-row">
              <span className="grow"><span className="title">{i.name}</span>{i.hinweis && <span className="meta">{i.hinweis}</span>}</span>
              <span className={`cc-state ${i.status}`}>{stateLabel(i.status)}</span>
            </li>
          ))}
        </ul>
      </article>
    </div>
  );
}
