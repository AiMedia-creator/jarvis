import { useState } from "react";
import type { CallOverview, CallRecord } from "./api";

const CRM_LABEL: Record<string, string> = { offen: "CRM offen", vorschau: "CRM-Vorschau", geschrieben: "im CRM", nicht_noetig: "kein CRM-Eintrag nötig" };
const PHONE_LABEL: Record<string, string> = { geschaeftlich: "geschäftlich", privat: "privat", unklar: "Nummer ungeklärt" };
const when = (s: number) => new Date(s * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const pct = (v: number | null) => (v == null ? "–" : `${String(v).replace(".", ",")}`);
/** Lokales Datum als JJJJ-MM-TT (nicht UTC – sonst kippt „fällig“ kurz nach Mitternacht). */
const localToday = () => { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };

function Row({ call, results, project }: { call: CallRecord; results: Record<string, string>; project: string }) {
  const risky = call.telefon_status !== "geschaeftlich";
  return (
    <li className={`cc-call res-${call.ergebnis}`}>
      <div className="cc-call-main">
        <strong>{call.firma || call.lead_name}</strong>
        {call.firma && call.lead_name ? <span> · {call.lead_name}{call.rolle ? `, ${call.rolle}` : ""}</span> : null}
        <span className="cc-call-res">{results[call.ergebnis] ?? call.ergebnis}</span>
      </div>
      {call.naechster_schritt
        ? <p className="cc-call-next">Nächster Schritt: {call.naechster_schritt}{call.frist ? ` (bis ${call.frist})` : " (ohne Frist)"}</p>
        : ["rueckruf", "interesse"].includes(call.ergebnis) && <p className="cc-call-next warn">Kein nächster Schritt dokumentiert.</p>}
      {call.notiz && <p className="cc-call-note">{call.notiz}</p>}
      <p className="cc-call-meta">
        {when(call.erstellt)} · {project} · {CRM_LABEL[call.crm_status] ?? call.crm_status}
        {call.auftrag_id ? ` · Auftrag ${call.auftrag_id}` : ""}
      </p>
      <p className="cc-call-meta">
        <span className={`cc-tag ${risky ? "risk" : ""}`}>{PHONE_LABEL[call.telefon_status] ?? call.telefon_status}</span>{" "}
        Kontaktquelle: {call.kontakt_quelle || "nicht angegeben"} · erfasst {when(call.erstellt)}
        {risky && " · Aus der Nummer folgt keine Einwilligung für Werbeanrufe."}
      </p>
    </li>
  );
}

type Tab = "pipeline" | "anrufe" | "vorbereiten";
const PREP: { skill: string; title: string; text: string; label: string; placeholder: string; prefix: string; mode: "job" | "chat" }[] = [
  { skill: "lead-recherche", title: "Lead und Entscheider recherchieren", text: "Firma prüfen, zuständigen Entscheider finden, Kontaktdaten nur mit Quelle und Abrufzeitpunkt. Ergebnis landet als Auftrag im Kanban.",
    label: "Firma (und Ort oder Website)", placeholder: "z. B. Muster GmbH, Köln, muster.de", prefix: "Recherchiere den Lead: ", mode: "job" },
  { skill: "cold-call-vorbereitung", title: "Anruf vorbereiten", text: "Mini-Karte, drei Gesprächseinstiege, wahrscheinliche Einwände mit Antworten, Ziel und Rückfallziel.",
    label: "Firma und Ansprechpartner", placeholder: "z. B. Muster GmbH, Frau Beispiel (Geschäftsführung)", prefix: "Bereite den Anruf vor bei: ", mode: "job" },
  { skill: "einwand-rollenspiel", title: "Einwände im Rollenspiel üben", text: "Jarvis spielt den Entscheider und wechselt Einwände; danach kurzes, belegtes Feedback. Läuft als Gespräch im Command Center.",
    label: "Was willst du üben?", placeholder: "z. B. Einstieg, „Keine Zeit“, „Schicken Sie was per Mail“", prefix: "Starte das Einwand-Rollenspiel (Skill einwand-rollenspiel). Ich will üben: ", mode: "chat" },
  { skill: "anruf-nachbereitung", title: "Anruf dokumentieren", text: "Ergebnis, Notiz, nächster Schritt mit Frist. CRM-Eintrag und Mail nur als Vorlage – gesendet wird erst nach deiner Freigabe.",
    label: "Kurzdiktat", placeholder: "z. B. Muster GmbH, Frau Beispiel, Interesse, Angebot bis Freitag schicken", prefix: "Anruf nachbereiten (Skill anruf-nachbereitung): ", mode: "chat" },
];

function PrepCard({ item, onAsk, onStartSkill }: { item: (typeof PREP)[number]; onAsk: (text: string) => void; onStartSkill: (skill: string, task: string) => Promise<void> }) {
  const [value, setValue] = useState("");
  const [state, setState] = useState<string | null>(null);
  const go = async () => {
    if (value.trim().length < 3) { setState("Bitte kurz angeben, worum es geht."); return; }
    if (item.mode === "chat") { onAsk(item.prefix + value.trim()); return; }
    setState("Startet …");
    try { await onStartSkill(item.skill, item.prefix + value.trim()); setValue(""); setState("Als Job gestartet – Fortschritt im Agenten-Center."); }
    catch (e) { setState(e instanceof Error ? e.message : "Start fehlgeschlagen."); }
  };
  return (
    <form className="cc-card-box sp-prep" onSubmit={(e) => { e.preventDefault(); void go(); }}>
      <h3>{item.title}</h3><p>{item.text}</p>
      <label htmlFor={`prep-${item.skill}`}>{item.label}</label>
      <input id={`prep-${item.skill}`} value={value} placeholder={item.placeholder} maxLength={300} onChange={(e) => setValue(e.target.value)} />
      <div className="cc-actions"><button type="submit" className="cc-btn small accent">{item.mode === "chat" ? "Im Gespräch starten" : "Als Job starten"}</button>
        {state && <span className="cc-hint" role="status">{state}</span>}</div>
    </form>
  );
}

/** Vertrieb: Pipeline, Anrufe mit Follow-ups, Vorbereitung. Gemessen wird nur, was Jarvis wirklich erfasst hat. */
export function CallsPanel({ data, error, onAsk, canAsk, days, onDays, onStartSkill }: {
  data: CallOverview | null; error: string | null; onAsk: (text: string) => void; canAsk: boolean; days: number; onDays: (d: number) => void;
  onStartSkill: (skill: string, task: string) => Promise<void>;
}) {
  const [tab, setTab] = useState<Tab>("pipeline");
  if (error) return <p className="cc-warn-text">{error}</p>;
  if (!data) return <p className="cc-empty">Wird geladen …</p>;
  const k = data.kennzahlen;
  const names = new Map(data.projekte.map((p) => [p.id, p.name]));
  const today = localToday();
  const stages: { id: string; label: string; match: (c: CallRecord) => boolean }[] = [
    { id: "offen", label: "Follow-up offen", match: (c) => !!c.naechster_schritt && c.ergebnis !== "termin" && c.ergebnis !== "abschluss" },
    { id: "termin", label: "Termin", match: (c) => c.ergebnis === "termin" },
    { id: "abschluss", label: "Abschluss", match: (c) => c.ergebnis === "abschluss" },
    { id: "ruhend", label: "Kein Interesse / nicht erreicht", match: (c) => ["kein_interesse", "nicht_erreicht", "falsche_nummer"].includes(c.ergebnis) },
  ];
  const due = data.letzte_anrufe.filter((c) => c.naechster_schritt && c.frist && c.frist <= today).sort((a, b) => a.frist.localeCompare(b.frist));
  const undocumented = data.letzte_anrufe.filter((c) => ["rueckruf", "interesse"].includes(c.ergebnis) && !c.naechster_schritt);
  const noProfile = data.projekte.filter((p) => p.aktiv && (!p.zielgruppe || !p.leitfaden));
  return (
    <div className="cc-calls">
      <div className="sp-head">
        <div className="cc-tabs" role="group" aria-label="Vertriebsansicht">
          <button type="button" aria-pressed={tab === "pipeline"} onClick={() => setTab("pipeline")}>Pipeline</button>
          <button type="button" aria-pressed={tab === "anrufe"} onClick={() => setTab("anrufe")}>Anrufe und Follow-ups{due.length ? ` (${due.length} fällig)` : ""}</button>
          <button type="button" aria-pressed={tab === "vorbereiten"} onClick={() => setTab("vorbereiten")}>Vorbereiten und üben</button>
        </div>
        {tab !== "vorbereiten" && (
          <div className="cc-period" role="group" aria-label="Zeitraum">
            {[7, 30, 90].map((d) => <button key={d} type="button" className={days === d ? "on" : ""} aria-pressed={days === d} onClick={() => onDays(d)}>{d} Tage</button>)}
          </div>
        )}
      </div>

      {tab === "pipeline" && (
        <>
          <ul className="cc-kpis" aria-label={`Kennzahlen der letzten ${k.zeitraum_tage} Tage`}>
            <li><strong>{k.gespraeche}</strong><span>Gespräche erfasst</span></li>
            <li><strong>{k.termine}</strong><span>Termine</span></li>
            <li><strong>{pct(k.termine_pro_100)}</strong><span>Termine pro 100</span></li>
            <li><strong>{k.followups_faellig}</strong><span>Follow-ups fällig</span></li>
            <li><strong>{k.crm_offen}</strong><span>CRM offen</span></li>
          </ul>
          <p className="cc-source">Quelle: {k.quelle}. Stand {new Date(k.stand).toLocaleString("de-DE")}. Zeitraum {k.zeitraum_tage} Tage.
            {k.gespraeche === 0 && " Noch kein Anruf erfasst – deshalb gibt es keine Quote."}</p>
          <section className="cc-card-box" aria-label="Pipeline">
            <header><h3>Pipeline-Stufen</h3></header>
            <div className="cc-pipeline">
              {stages.map((st) => {
                const items = data.letzte_anrufe.filter(st.match);
                return (
                  <div key={st.id} className="cc-pipe-col">
                    <h4>{st.label} <span className="mono">{items.length}</span></h4>
                    {items.length === 0 ? <p className="cc-empty">leer</p>
                      : <ul>{items.slice(0, 8).map((c) => <li key={c.id}>{c.firma || c.lead_name}{c.naechster_schritt ? ` – ${c.naechster_schritt}` : ""}</li>)}</ul>}
                  </div>
                );
              })}
            </div>
            <p className="cc-source">Stufen ergeben sich aus dem Ergebnis des jeweils erfassten Anrufs (höchstens 30 angezeigt). Keine CRM-Daten, keine Umsatzprognose.</p>
          </section>
          <section className="cc-card-box">
            <header><h3>Projekte</h3></header>
            {data.projekte.length === 0 ? (
              <p className="cc-empty">Noch kein Vertriebsprojekt. Ohne Projekt (Angebot, Zielgruppe, Leitfaden) kann Jarvis keine Anrufe vorbereiten.
                {canAsk && <> <button type="button" className="cc-link" onClick={() => onAsk("Lege ein neues Vertriebsprojekt an. Frag mich nach Name, Angebot, Zielgruppe, Leitfaden und CRM.")}>Projekt mit Jarvis anlegen</button></>}</p>
            ) : (
              <ul className="cc-projects">
                {data.projekte.map((p) => (
                  <li key={p.id}><strong>{p.name}</strong> <span className="cc-call-meta">CRM: {p.crm}{p.aktiv ? "" : " · inaktiv"}</span>
                    <p className="cc-call-note">Zielgruppe: {p.zielgruppe || "nicht hinterlegt"} · Leitfaden: {p.leitfaden ? "vorhanden" : "nicht hinterlegt"}</p></li>
                ))}
              </ul>
            )}
            {noProfile.length > 0 && <p className="cc-hint">Bei {noProfile.length} Projekt{noProfile.length === 1 ? "" : "en"} fehlen Zielgruppe oder Leitfaden – Vorbereitung und Coaching bleiben dort allgemein.</p>}
          </section>
        </>
      )}

      {tab === "anrufe" && (
        <>
          <ul className="cc-kpis" aria-label="Offene Punkte">
            <li><strong>{due.length}</strong><span>Follow-ups fällig</span></li>
            <li><strong>{k.ohne_naechsten_schritt ?? undocumented.length}</strong><span>Rückruf/Interesse ohne nächsten Schritt</span></li>
            <li><strong>{k.crm_offen}</strong><span>ohne CRM-Eintrag</span></li>
            <li><strong>{k.telefon_ungeklaert ?? "–"}</strong><span>Nummer privat/ungeklärt</span></li>
          </ul>
          <section className="cc-card-box" aria-label="Fällige Follow-ups">
            <header><h3>Fällige Follow-ups</h3></header>
            {due.length === 0 ? <p className="cc-empty">Keine fälligen Follow-ups in den erfassten Anrufen.</p> : (
              <ul className="cc-call-list">
                {due.map((c) => (
                  <li key={c.id} className="cc-call"><strong>{c.naechster_schritt}</strong><span> · {c.firma || c.lead_name} · fällig seit {c.frist}</span>
                    {canAsk && <div className="cc-actions"><button type="button" className="cc-btn small" onClick={() => onAsk(`Bereite das Follow-up vor: ${c.naechster_schritt} bei ${c.firma || c.lead_name}. Mail nur als Entwurf, nichts senden.`)}>Follow-up vorbereiten</button></div>}</li>
                ))}
              </ul>
            )}
          </section>
          <section className="cc-card-box" aria-label="Ohne dokumentierten nächsten Schritt">
            <header><h3>Ohne dokumentierten nächsten Schritt</h3></header>
            {undocumented.length === 0 ? <p className="cc-empty">Alle Gespräche mit Rückruf oder Interesse haben einen nächsten Schritt.</p> : (
              <ul className="cc-call-list">{undocumented.map((c) => <li key={c.id} className="cc-call"><strong>{c.firma || c.lead_name}</strong><span> · {data.ergebnisse[c.ergebnis] ?? c.ergebnis} am {when(c.erstellt)}</span></li>)}</ul>
            )}
            <p className="cc-source">Jarvis kennt nur Anrufe, die du diktiert hast. Geführte, aber nicht erfasste Anrufe tauchen nirgends auf – es gibt keine Telefonanlagen-Anbindung.</p>
          </section>
          <section className="cc-card-box">
            <header><h3>Erfasste Anrufe</h3></header>
            {data.letzte_anrufe.length === 0 ? (
              <p className="cc-empty">Noch kein Anruf erfasst. Nach einem Gespräch kurz diktieren: „Anruf bei …, Ergebnis …, nächster Schritt … bis …“.
                {canAsk && <> <button type="button" className="cc-link" onClick={() => onAsk("Ich diktiere dir gleich das Ergebnis eines Anrufs. Frag nach, was fehlt.")}>Diktat starten</button></>}</p>
            ) : <ul className="cc-call-list">{data.letzte_anrufe.map((c) => <Row key={c.id} call={c} results={data.ergebnisse} project={names.get(c.projekt_id) ?? c.projekt_id} />)}</ul>}
          </section>
        </>
      )}

      {tab === "vorbereiten" && (
        <>
          <p className="cc-source">Ablauf: Lead → Recherche → Entscheider → Vorbereitung → Anruf → Ergebnis dokumentieren → Follow-up → CRM → nächste Umsatzaktion.</p>
          {canAsk ? <div className="cc-grid two">{PREP.map((p) => <PrepCard key={p.skill} item={p} onAsk={onAsk} onStartSkill={onStartSkill} />)}</div>
            : <p className="cc-empty">Nur Ansicht: Ein anderes Jarvis-Fenster hält gerade die Steuerung.</p>}
          <section className="cc-card-box sp-legal" aria-label="Rechtlicher Rahmen">
            <header><h3>Kaltakquise in Deutschland – worauf Jarvis achtet</h3></header>
            <ul>
              <li>Verbraucher (B2C): Werbeanrufe nur mit vorheriger ausdrücklicher Einwilligung (§ 7 Abs. 2 Nr. 1 UWG); die Einwilligung muss dokumentiert und fünf Jahre aufbewahrt werden (§ 7a UWG).</li>
              <li>Unternehmen (B2B): nur bei mutmaßlicher Einwilligung – es braucht einen konkreten sachlichen Bezug zum Geschäft des Angerufenen. Eine öffentlich auffindbare Nummer allein genügt nicht.</li>
              <li>Rufnummer nicht unterdrücken (§ 15 Abs. 2 TTDSG/TDDDG). Widerspruch respektieren und festhalten.</li>
              <li>Private oder ungeklärte Nummern kennzeichnet Jarvis und empfiehlt sie nicht zum Anruf.</li>
            </ul>
            <p className="cc-source">Keine Rechtsberatung. Stand der Hinweise: Gesetzeslage nach UWG § 7/§ 7a; im Zweifel vor einer Kampagne rechtlich prüfen lassen.</p>
          </section>
        </>
      )}
    </div>
  );
}
