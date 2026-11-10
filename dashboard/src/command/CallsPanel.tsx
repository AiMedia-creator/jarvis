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
export function CallsPanel({ data, error, onAsk, canAsk }: { data: CallOverview | null; error: string | null; onAsk: (text: string) => void; canAsk: boolean }) {
  if (error) return <p className="cc-warn-text">{error}</p>;
  if (!data) return <p className="cc-empty">Wird geladen …</p>;
  const k = data.kennzahlen;
  const names = new Map(data.projekte.map((p) => [p.id, p.name]));
  return (
    <div className="cc-calls">
      <ul className="cc-kpis" aria-label={`Kennzahlen der letzten ${k.zeitraum_tage} Tage`}>
        <li><strong>{k.gespraeche}</strong><span>Gespräche</span></li>
        <li><strong>{k.termine}</strong><span>Termine</span></li>
        <li><strong>{pct(k.termine_pro_100)}</strong><span>Termine pro 100</span></li>
        <li><strong>{k.crm_offen}</strong><span>CRM offen</span></li>
        <li><strong>{k.followups_faellig}</strong><span>Follow-ups fällig</span></li>
      </ul>
      <p className="cc-source">Quelle: {k.quelle}. Stand {new Date(k.stand).toLocaleString("de-DE")}. Zeitraum {k.zeitraum_tage} Tage.</p>
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
