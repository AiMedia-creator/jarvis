import type { CallOverview, CallRecord } from "./api";

const CRM_LABEL: Record<string, string> = { offen: "CRM offen", vorschau: "CRM-Vorschau", geschrieben: "im CRM", nicht_noetig: "kein CRM-Eintrag nötig" };
const PHONE_LABEL: Record<string, string> = { geschaeftlich: "geschäftlich", privat: "privat", unklar: "Nummer unklar" };
const when = (s: number) => new Date(s * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const pct = (v: number | null) => (v == null ? "–" : `${String(v).replace(".", ",")}`);

function Row({ call, results, project }: { call: CallRecord; results: Record<string, string>; project: string }) {
  return (
    <li className={`cc-call res-${call.ergebnis}`}>
      <div className="cc-call-main">
        <strong>{call.firma || call.lead_name}</strong>
        {call.firma && call.lead_name ? <span> · {call.lead_name}{call.rolle ? `, ${call.rolle}` : ""}</span> : null}
        <span className="cc-call-res">{results[call.ergebnis] ?? call.ergebnis}</span>
      </div>
      {call.naechster_schritt && <p className="cc-call-next">Nächster Schritt: {call.naechster_schritt}{call.frist ? ` (bis ${call.frist})` : ""}</p>}
      {call.notiz && <p className="cc-call-note">{call.notiz}</p>}
      <p className="cc-call-meta">
        {when(call.erstellt)} · {project} · {PHONE_LABEL[call.telefon_status] ?? call.telefon_status} · {CRM_LABEL[call.crm_status] ?? call.crm_status}
        {call.auftrag_id ? ` · Auftrag ${call.auftrag_id}` : ""}
      </p>
    </li>
  );
}

/** Anruf-Copilot: gemessene Zahlen und letzte Anrufe. Nur lesend; erfasst wird per Sprache oder Text über Jarvis. */
export function CallsPanel({ data, error, onAsk, canAsk, days, onDays }: { data: CallOverview | null; error: string | null; onAsk: (text: string) => void; canAsk: boolean; days: number; onDays: (d: number) => void }) {
  if (error) return <p className="cc-warn-text">{error}</p>;
  if (!data) return <p className="cc-empty">Wird geladen …</p>;
  const k = data.kennzahlen;
  const names = new Map(data.projekte.map((p) => [p.id, p.name]));
  const today = new Date().toISOString().slice(0, 10);
  const stages: { id: string; label: string; match: (c: CallRecord) => boolean }[] = [
    { id: "offen", label: "Follow-up offen", match: (c) => !!c.naechster_schritt && c.ergebnis !== "termin" && c.ergebnis !== "abschluss" },
    { id: "termin", label: "Termin", match: (c) => c.ergebnis === "termin" },
    { id: "abschluss", label: "Abschluss", match: (c) => c.ergebnis === "abschluss" },
    { id: "ruhend", label: "Kein Interesse / nicht erreicht", match: (c) => ["kein_interesse", "nicht_erreicht", "falsche_nummer"].includes(c.ergebnis) },
  ];
  const due = data.letzte_anrufe.filter((c) => c.naechster_schritt && c.frist && c.frist <= today);
  return (
    <div className="cc-calls">
      <div className="cc-period" role="group" aria-label="Zeitraum">
        {[7, 30, 90].map((d) => (
          <button key={d} type="button" className={days === d ? "on" : ""} aria-pressed={days === d} onClick={() => onDays(d)}>{d} Tage</button>
        ))}
      </div>
      <ul className="cc-kpis" aria-label={`Kennzahlen der letzten ${k.zeitraum_tage} Tage`}>
        <li><strong>{k.gespraeche}</strong><span>Gespräche</span></li>
        <li><strong>{k.termine}</strong><span>Termine</span></li>
        <li><strong>{pct(k.termine_pro_100)}</strong><span>Termine pro 100</span></li>
        <li><strong>{k.crm_offen}</strong><span>CRM offen</span></li>
        <li><strong>{k.followups_faellig}</strong><span>Follow-ups fällig</span></li>
      </ul>
      <p className="cc-source">Quelle: {k.quelle}. Stand {new Date(k.stand).toLocaleString("de-DE")}. Zeitraum {k.zeitraum_tage} Tage.</p>
      <section className="cc-card-box" aria-label="Nächste Umsatzaktion">
        <header><h3>Nächste Umsatzaktion</h3></header>
        {due.length === 0 ? <p className="cc-empty">Keine fälligen Follow-ups in den erfassten Anrufen.</p> : (
          <ul className="cc-call-list">
            {due.slice(0, 5).map((c) => (
              <li key={c.id} className="cc-call"><strong>{c.naechster_schritt}</strong><span> · {c.firma || c.lead_name} (bis {c.frist})</span></li>
            ))}
          </ul>
        )}
      </section>
      <section className="cc-card-box" aria-label="Pipeline">
        <header><h3>Pipeline</h3></header>
        <div className="cc-pipeline">
          {stages.map((st) => {
            const items = data.letzte_anrufe.filter(st.match);
            return (
              <div key={st.id} className="cc-pipe-col">
                <h4>{st.label} <span className="mono">{items.length}</span></h4>
                <ul>{items.slice(0, 8).map((c) => <li key={c.id}>{c.firma || c.lead_name}{c.naechster_schritt ? ` – ${c.naechster_schritt}` : ""}</li>)}</ul>
              </div>
            );
          })}
        </div>
        <p className="cc-source">Aus den zuletzt erfassten Anrufen (höchstens 30 angezeigt); keine CRM-Daten.</p>
      </section>
      <section className="cc-card-box">
        <header><h3>Projekte</h3></header>
        {data.projekte.length === 0 ? (
          <p className="cc-empty">Noch kein Projekt. Sag Jarvis zum Beispiel: „Leg das Projekt … an, CRM HubSpot.“
            {canAsk && <> <button type="button" className="cc-link" onClick={() => onAsk("Lege ein neues Vertriebsprojekt an. Frag mich nach Name und CRM.")}>Jarvis fragen</button></>}</p>
        ) : (
          <ul className="cc-projects">
            {data.projekte.map((p) => (
              <li key={p.id}><strong>{p.name}</strong> <span className="cc-call-meta">CRM: {p.crm}{p.aktiv ? "" : " · inaktiv"}</span>
                {p.zielgruppe && <p className="cc-call-note">Zielgruppe: {p.zielgruppe}</p>}</li>
            ))}
          </ul>
        )}
      </section>
      <section className="cc-card-box">
        <header><h3>Letzte Anrufe</h3></header>
        {data.letzte_anrufe.length === 0 ? (
          <p className="cc-empty">Noch kein Anruf erfasst. Nach einem Gespräch kurz diktieren: „Anruf bei … , Ergebnis …“.
            {canAsk && <> <button type="button" className="cc-link" onClick={() => onAsk("Ich diktiere dir gleich das Ergebnis eines Anrufs. Frag nach, was fehlt.")}>Diktat starten</button></>}</p>
        ) : (
          <ul className="cc-call-list">
            {data.letzte_anrufe.map((c) => <Row key={c.id} call={c} results={data.ergebnisse} project={names.get(c.projekt_id) ?? c.projekt_id} />)}
          </ul>
        )}
      </section>
    </div>
  );
}
