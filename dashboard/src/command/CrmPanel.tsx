import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ApiError, type Lead, type LeadActivity, type LeadImportReport, type LeadOutcome, type LeadOverview, type LeadSession, type LeadStatusDef } from "./api";
import { Icon } from "./icons";

/** Ziel der Call-Session. Ein Richtwert für die Anzeige, keine Messung. */
export const SESSION_TARGET = 20;
const SESSION_KEY = "jarvis-crm-session-v1";
/** Leads je Seite. Gezählt, gesucht und gefiltert wird immer über den ganzen Bestand (Server). */
export const PAGE_SIZE = 50;
type Tab = "leads" | "session" | "auswertung";
type Draft = { kind: "follow_up"; date: string; step: string } | { kind: "meeting_gebucht"; start: string; calendar: boolean } | null;

const localDate = (offsetDays = 0) => {
  const d = new Date(Date.now() + offsetDays * 86_400_000);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};
const when = (ts: number | null) => ts ? new Date(ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "–";
const dateDe = (iso: string) => iso ? new Date(`${iso.slice(0, 10)}T00:00:00`).toLocaleDateString("de-DE", { weekday: "short", day: "2-digit", month: "2-digit" }) : "";
const dateTimeDe = (iso: string) => iso ? `${dateDe(iso)} ${iso.slice(11, 16)}` : "";
export const leadLabel = (lead: Pick<Lead, "firma" | "name">) => [lead.firma, lead.name].filter(Boolean).join(" · ") || "Ohne Namen";
const clockOf = (seconds: number) => `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
const isTyping = (target: EventTarget | null) => {
  const el = target as HTMLElement | null;
  return !!el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable);
};
const safeUrl = (value: string) => (/^https?:\/\//i.test(value) ? value : `https://${value}`);

function loadSession(): number | null {
  try {
    const start = Number(JSON.parse(localStorage.getItem(SESSION_KEY) || "null")?.start);
    return Number.isFinite(start) && Date.now() / 1000 - start < 12 * 3600 ? start : null;
  } catch { return null; }
}

/** Status überall gleich: neutraler Chip, Punkt in Statusfarbe, Text. */
export function LeadStatus({ status, defs }: { status: string; defs: LeadStatusDef[] }) {
  const def = defs.find((d) => d.id === status);
  return <span className={`cc-state crm-st ${def?.ton ?? "neutral"}`}>{def?.titel ?? status}</span>;
}

type NewFields = { firma: string; name: string; rolle: string; telefon: string; email: string; website: string; notiz: string };
const EMPTY: NewFields = { firma: "", name: "", rolle: "", telefon: "", email: "", website: "", notiz: "" };
/** Dieselben Regeln wie jarvis/leads.py – hier nur, damit der Fehler sofort am Feld steht. Der Server prüft erneut. */
export function validateLead(f: NewFields): Partial<Record<keyof NewFields, string>> {
  const e: Partial<Record<keyof NewFields, string>> = {};
  if (!f.firma.trim() && !f.name.trim()) e.firma = "Firma oder Ansprechpartner angeben.";
  if (f.telefon.trim() && f.telefon.replace(/\D/g, "").length < 6) e.telefon = "Mindestens 6 Ziffern.";
  if (f.email.trim() && !/^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$/.test(f.email.trim())) e.email = "Adresse wie name@firma.de.";
  if (f.website.trim() && !/^(https?:\/\/)?[\w.-]+\.[a-z]{2,}(\/\S*)?$/i.test(f.website.trim())) e.website = "Adresse wie firma.de.";
  return e;
}

/** „Lead anlegen“: drei kurze Gruppen links, rechts die Karte, wie sie gleich auf der Anrufliste steht. */
function NewLead({ onCreated, onOpen, onCancel }: { onCreated: (lead: Lead) => void; onOpen: (id: string) => void; onCancel: () => void }) {
  const [f, setF] = useState<NewFields>(EMPTY);
  const [touched, setTouched] = useState<Partial<Record<keyof NewFields, boolean>>>({});
  const [tried, setTried] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dup, setDup] = useState<{ lead: Lead; hard: boolean } | null>(null);
  const [done, setDone] = useState<Lead | null>(null);
  const summary = useRef<HTMLParagraphElement>(null);
  const first = useRef<HTMLInputElement>(null);
  const errors = validateLead(f);
  const invalid = Object.keys(errors).length > 0;
  const filled = (Object.keys(EMPTY) as (keyof NewFields)[]).filter((k) => f[k].trim()).length;
  useEffect(() => { first.current?.focus(); }, [done]);

  const submit = async (confirmed = false) => {
    setTried(true);
    if (invalid) { setError(errors.firma ? "Firma oder Name ist nötig." : "Bitte die markierten Felder korrigieren."); window.setTimeout(() => summary.current?.focus(), 0); return; }
    setBusy(true); setError(null);
    try {
      const { lead } = await api.createLead(f, confirmed);
      setDone(lead); setDup(null); onCreated(lead);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && e.body?.duplikat) { setDup({ lead: e.body.duplikat as Lead, hard: !!e.body.hart }); setError(e.message); }
      else setError(e instanceof Error ? e.message : "Lead nicht angelegt.");
      window.setTimeout(() => summary.current?.focus(), 0);
    } finally { setBusy(false); }
  };
  const reset = () => { setF(EMPTY); setTouched({}); setTried(false); setError(null); setDup(null); setDone(null); };

  const field = (key: keyof NewFields, label: string, opts: { type?: string; hint?: string; wide?: boolean; ref?: React.Ref<HTMLInputElement> } = {}) => {
    const message = (tried || touched[key]) ? errors[key] : undefined;
    return (
      <label className={`nl-field ${opts.wide ? "wide" : ""} ${message ? "bad" : ""}`}>{label}
        <input ref={opts.ref} type={opts.type ?? "text"} value={f[key]} autoComplete="off" maxLength={key === "notiz" ? 4000 : 200} placeholder={opts.hint}
          aria-invalid={message ? true : undefined} aria-describedby={message ? `nl-err-${key}` : undefined}
          onBlur={() => setTouched((t) => ({ ...t, [key]: true }))}
          onChange={(e) => { setF({ ...f, [key]: e.target.value }); setDup(null); if (error && !dup) setError(null); }} />
        {message && <span className="nl-err" id={`nl-err-${key}`}>{message}</span>}
      </label>
    );
  };
  const preview = (
    <aside className="nl-preview" aria-label="Vorschau des Leads">
      <span className="nl-kicker">{done ? "Angelegt" : "So steht er auf der Anrufliste"}</span>
      <strong className={f.firma || f.name || done ? "" : "ghost"}>{done ? done.firma || done.name : f.firma.trim() || f.name.trim() || "Firma"}</strong>
      <span className="nl-who">{done ? [done.firma && done.name, done.rolle].filter(Boolean).join(", ") || " " : [f.firma.trim() && f.name.trim(), f.rolle.trim()].filter(Boolean).join(", ") || "Ansprechpartner, Rolle"}</span>
      <span className="nl-line mono">{(done?.telefon ?? f.telefon.trim()) || "Telefon fehlt noch"}</span>
      <span className="nl-state"><i aria-hidden="true" />Neu</span>
      {!done && <span className="nl-meter" role="img" aria-label={`${filled} von 7 Angaben ausgefüllt`}>
        {(Object.keys(EMPTY) as (keyof NewFields)[]).map((k) => <i key={k} className={f[k].trim() ? "on" : ""} />)}</span>}
    </aside>
  );

  if (done) return (
    <section className="nl done" aria-label="Lead angelegt">
      <div className="nl-main">
        <p className="crm-ok nl-success" role="status"><span className="nl-check" aria-hidden="true" />Lead angelegt: {leadLabel(done)}</p>
        <div className="cc-actions">
          <button type="button" className="cc-btn brand" onClick={() => onOpen(done.id)}>Lead öffnen</button>
          <button type="button" className="cc-btn" onClick={reset}>Weiteren anlegen</button>
          <button type="button" className="cc-btn" onClick={onCancel}>Fertig</button>
        </div>
      </div>
      {preview}
    </section>
  );
  return (
    <form className="nl" aria-label="Neuen Lead anlegen" noValidate onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <div className="nl-main">
        <h3>Neuer Lead</h3>
        <fieldset><legend><b>1</b> Wer</legend>
          <div className="nl-grid">{field("firma", "Firma", { ref: first })}{field("name", "Ansprechpartner")}{field("rolle", "Rolle", { hint: "z. B. Geschäftsführung" })}</div></fieldset>
        <fieldset><legend><b>2</b> Erreichbar</legend>
          <div className="nl-grid">{field("telefon", "Telefon", { type: "tel" })}{field("email", "E-Mail", { type: "email" })}{field("website", "Website")}</div></fieldset>
        <fieldset><legend><b>3</b> Merken</legend>
          <div className="nl-grid">{field("notiz", "Notiz", { wide: true, hint: "Was du vor dem ersten Anruf wissen willst" })}</div></fieldset>
        {error && <p className="ac-err nl-summary" role="alert" tabIndex={-1} ref={summary}>{error}</p>}
        {dup && (
          <p className="crm-dup" role="status">Vorhanden: <strong>{leadLabel(dup.lead)}</strong>.{" "}
            <button type="button" className="cc-link" onClick={() => onOpen(dup.lead.id)}>Vorhandenen Lead öffnen</button>
            {!dup.hard && <button type="button" className="cc-link" disabled={busy} onClick={() => void submit(true)}>Trotzdem als neuen Lead anlegen</button>}
          </p>
        )}
        <div className="cc-actions">
          <button type="submit" className="cc-btn brand" disabled={busy}>{busy ? "Speichert …" : "Lead anlegen"}</button>
          <button type="button" className="cc-btn" onClick={onCancel}>Abbrechen</button>
        </div>
      </div>
      {preview}
    </form>
  );
}

const mb = (bytes: number) => `${(bytes / 1_000_000).toLocaleString("de-DE", { maximumFractionDigits: 1 })} MB`;

function CsvImport({ columns, maxBytes, maxRows, onDone, onCancel }: { columns: string[]; maxBytes: number; maxRows: number; onDone: (count: number) => void; onCancel: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<LeadImportReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const check = async (next: File) => {
    setError(null); setReport(null); setFile(next);
    if (next.size > maxBytes) { setError(`Die Datei ist zu groß: ${mb(next.size)} (höchstens ${mb(maxBytes)} und ${maxRows} Zeilen).`); return; }
    setBusy(true);
    try { setReport(await api.importLeads(next, false)); }   // rohe Bytes: der Server prüft Größe, UTF-8 und jede Zeile
    catch (e) { setError(e instanceof Error ? e.message : "Datei nicht lesbar."); }
    finally { setBusy(false); }
  };
  const run = async () => {
    if (!file) return;
    setBusy(true); setError(null);
    try { const done = await api.importLeads(file, true); onDone(done.importiert); }
    catch (e) { setError(e instanceof Error ? e.message : "Import fehlgeschlagen."); }
    finally { setBusy(false); }
  };
  return (
    <section className="crm-form" aria-label="Leads aus CSV importieren">
      <h3>CSV-Import</h3>
      <p className="crm-limits"><span className="cc-tag">UTF-8</span><span className="cc-tag">bis {mb(maxBytes)}</span><span className="cc-tag">bis {maxRows} Zeilen</span>
        <span className="cc-tag">erst prüfen, dann importieren</span></p>
      <details className="crm-more"><summary>Spalten und Regeln</summary>
        <p>Kopfzeile mit {columns.join(", ")} (mindestens firma oder name). Trennzeichen Komma oder Semikolon.
          Bei einem Zeilenfehler wird nichts geschrieben. Duplikate werden übersprungen und genannt.</p></details>
      <label>CSV-Datei<input type="file" accept=".csv,text/csv" onChange={(e) => { const next = e.target.files?.[0]; if (next) void check(next); }} /></label>
      {busy && <p className="cc-empty" role="status">Wird geprüft …</p>}
      {error && <p className="ac-err" role="alert">{error}</p>}
      {report && (
        <div role="status">
          <p><strong>{file?.name}</strong>: {report.neu} neu · {report.duplikate_gesamt} Duplikate übersprungen · {report.fehler_gesamt} Zeilen mit Fehler</p>
          {report.fehler.length > 0 && <ul className="crm-report">{report.fehler.slice(0, 8).map((x) => <li key={x.zeile}>Zeile {x.zeile}: {x.grund}</li>)}
            {report.fehler_gesamt > 8 && <li>… und {report.fehler_gesamt - 8} weitere</li>}</ul>}
          {report.duplikate.length > 0 && <ul className="crm-report">{report.duplikate.slice(0, 8).map((x) => <li key={x.zeile}>Zeile {x.zeile}: {x.lead} – {x.grund}</li>)}
            {report.duplikate_gesamt > 8 && <li>… und {report.duplikate_gesamt - 8} weitere</li>}</ul>}
          {report.fehler_gesamt > 0 && <p className="cc-warn-text">Fehlerzeilen korrigieren und die Datei erneut wählen.</p>}
        </div>
      )}
      <div className="cc-actions">
        <button type="button" className="cc-btn accent" disabled={busy || !report || report.fehler_gesamt > 0 || report.neu === 0} onClick={() => void run()}>
          {report && report.neu > 0 ? `${report.neu} Leads importieren` : "Importieren"}</button>
        <button type="button" className="cc-btn" onClick={onCancel}>Schließen</button>
      </div>
    </section>
  );
}

/** Lead-Detail: Kontakt, Firma, Status, Notizen, letzte Aktivität, nächster Schritt – und das Ergebnis mit einem Klick. */
function LeadDetail({ lead, defs, position, total, canEdit, onPrev, onNext, onSaved, onChanged, onOpenApprovals, session }: {
  lead: Lead; defs: LeadStatusDef[]; position: number; total: number; canEdit: boolean; onPrev: () => void; onNext: () => void;
  onSaved: (lead: Lead, message: string) => void; onChanged: (lead: Lead) => void; onOpenApprovals: () => void; session: boolean;
}) {
  const [note, setNote] = useState("");
  const [draft, setDraft] = useState<Draft>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [calendarInfo, setCalendarInfo] = useState<{ status: string; hinweis: string } | null>(null);
  const [acts, setActs] = useState<LeadActivity[] | null>(null);
  const [edit, setEdit] = useState<Partial<Lead> | null>(null);
  const noteRef = useRef<HTMLInputElement>(null);
  useEffect(() => { setNote(""); setDraft(null); setError(null); setCalendarInfo(null); setEdit(null); setActs(null); }, [lead.id]);
  useEffect(() => {
    let alive = true;
    api.lead(lead.id).then((d) => { if (alive) setActs(d.aktivitaeten); }).catch(() => { if (alive) setActs([]); });
    return () => { alive = false; };
  }, [lead.id, lead.version]);

  const fail = (e: unknown, fallback: string) => {
    if (e instanceof ApiError && e.status === 409 && e.body?.aktuell) { onChanged(e.body.aktuell as Lead); setError("Der Lead wurde inzwischen geändert. Der aktuelle Stand ist geladen – bitte erneut wählen."); }
    else setError(e instanceof Error ? e.message : fallback);
  };
  const record = async (outcome: LeadOutcome) => {
    if (!canEdit || busy) return;
    setBusy(true); setError(null); setCalendarInfo(null);
    try {
      const result = await api.leadOutcome(lead, { ...outcome, notiz: note.trim() });
      if (result.kalender) setCalendarInfo(result.kalender);
      const title = defs.find((d) => d.id === outcome.status)?.titel ?? outcome.status;
      onSaved(result.lead, `Gespeichert: ${title} · ${leadLabel(result.lead)}${result.kalender ? ` · ${result.kalender.hinweis}` : ""}`);
      setNote(""); setDraft(null);
    } catch (e) { fail(e, "Ergebnis nicht gespeichert."); }
    finally { setBusy(false); }
  };
  const choose = (status: string) => {
    if (status === "follow_up") setDraft({ kind: "follow_up", date: localDate(1), step: "" });
    else if (status === "meeting_gebucht") setDraft({ kind: "meeting_gebucht", start: `${localDate(1)}T10:00`, calendar: true });
    else void record({ status });
  };
  const saveNote = async () => {
    if (!note.trim() || busy) return;
    setBusy(true); setError(null);
    try { const r = await api.leadNote(lead, note.trim()); onChanged(r.lead); setNote(""); }
    catch (e) { fail(e, "Notiz nicht gespeichert."); }
    finally { setBusy(false); }
  };
  const saveEdit = async () => {
    if (!edit) return;
    setBusy(true); setError(null);
    try { const r = await api.updateLead(lead, edit); onChanged(r.lead); setEdit(null); }
    catch (e) { fail(e, "Änderung nicht gespeichert."); }
    finally { setBusy(false); }
  };
  const outcomes = defs.filter((d) => d.ergebnis);

  // Tastatur: Pfeile blättern, 1–5 wählen das Ergebnis, N springt in die Notiz (nur außerhalb von Feldern).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      // Solange ein Termin-Entwurf, die Stammdaten-Bearbeitung oder ein Speichern offen ist, löst keine Taste etwas aus.
      if (event.metaKey || event.ctrlKey || event.altKey || isTyping(event.target) || draft || edit || busy) return;
      if (event.key === "ArrowLeft") { event.preventDefault(); onPrev(); }
      else if (event.key === "ArrowRight") { event.preventDefault(); onNext(); }
      else if (event.key === "n" || event.key === "N") { event.preventDefault(); noteRef.current?.focus(); }
      else if (canEdit && /^[1-9]$/.test(event.key) && outcomes[Number(event.key) - 1]) { event.preventDefault(); choose(outcomes[Number(event.key) - 1].id); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const row = (label: string, value: ReactNode) => <><dt>{label}</dt><dd>{value || "–"}</dd></>;
  return (
    <article className="crm-detail" aria-label={`Lead ${leadLabel(lead)}`}>
      <header className="crm-detail-head">
        <button type="button" className="crm-arrow" onClick={onPrev} disabled={total < 2} aria-label="Vorheriger Lead (Pfeil links)" title="Vorheriger Lead (←)"><Icon name="arrowLeft" /></button>
        <div className="crm-detail-title">
          <h2>{lead.firma || lead.name || "Ohne Namen"}</h2>
          <p>{lead.firma && lead.name ? `${lead.name}${lead.rolle ? `, ${lead.rolle}` : ""}` : lead.rolle || "Kein Ansprechpartner hinterlegt"}</p>
        </div>
        <span className="crm-pos" aria-label={`Lead ${position} von ${total}`}>{position ? position.toLocaleString("de-DE") : "–"}/{total.toLocaleString("de-DE")}</span>
        <button type="button" className="crm-arrow" onClick={onNext} disabled={total < 2} aria-label="Nächster Lead (Pfeil rechts)" title="Nächster Lead (→)"><Icon name="arrowRight" /></button>
      </header>

      <div className="crm-detail-cols">
        <section aria-label="Kontakt und Unternehmen">
          <dl className="crm-facts">
            {row("Status", <LeadStatus status={lead.status} defs={defs} />)}
            {row("Telefon", lead.telefon && <><a className="crm-tel" href={`tel:${lead.telefon.replace(/[^+\d]/g, "")}`}>{lead.telefon}</a>
              {lead.telefon_status !== "geschaeftlich" && <span className="cc-tag risk">{lead.telefon_status === "privat" ? "privat" : "Nummer ungeklärt"}</span>}</>)}
            {row("E-Mail", lead.email && <a href={`mailto:${lead.email}`}>{lead.email}</a>)}
            {row("Website", lead.website && <a href={safeUrl(lead.website)} target="_blank" rel="noopener noreferrer">{lead.website}</a>)}
            {row("Ort", lead.ort)}
            {row("Quelle", lead.quelle)}
            {row("Letzte Aktivität", lead.letzte_aktivitaet ? `${when(lead.letzte_aktivitaet)} · ${lead.letzte_aktivitaet_text}` : "Noch keine")}
            {row("Nächster Schritt", lead.naechster_schritt ? `${lead.naechster_schritt}${lead.followup_am ? ` · ${dateDe(lead.followup_am)}` : ""}` : lead.termin_beginn ? `Meeting ${dateTimeDe(lead.termin_beginn)}` : "")}
            {lead.termin_beginn && row("Termin", `${dateTimeDe(lead.termin_beginn)} bis ${lead.termin_ende.slice(11, 16)}`)}
            {row("Anrufe erfasst", String(lead.anrufe))}
          </dl>
          {lead.notiz && <p className="crm-note"><strong>Notiz:</strong> {lead.notiz}</p>}
          {canEdit && !edit && <button type="button" className="cc-link" onClick={() => setEdit({ firma: lead.firma, name: lead.name, rolle: lead.rolle, telefon: lead.telefon, email: lead.email, website: lead.website, ort: lead.ort, notiz: lead.notiz })}>Stammdaten bearbeiten</button>}
          {edit && (
            <form className="crm-form" aria-label="Stammdaten bearbeiten" onSubmit={(e) => { e.preventDefault(); void saveEdit(); }}>
              <div className="crm-form-grid">
                {(["firma", "name", "rolle", "telefon", "email", "website", "ort"] as const).map((key) => (
                  <label key={key}>{key === "name" ? "Ansprechpartner" : key === "email" ? "E-Mail" : key[0].toUpperCase() + key.slice(1)}
                    <input value={(edit[key] as string) ?? ""} maxLength={200} onChange={(e) => setEdit({ ...edit, [key]: e.target.value })} /></label>
                ))}
              </div>
              <label>Notiz<textarea rows={2} value={edit.notiz ?? ""} maxLength={4000} onChange={(e) => setEdit({ ...edit, notiz: e.target.value })} /></label>
              <div className="cc-actions"><button type="submit" className="cc-btn small accent" disabled={busy}>Speichern</button>
                <button type="button" className="cc-btn small" onClick={() => setEdit(null)}>Verwerfen</button></div>
            </form>
          )}
        </section>

        <section aria-label="Ergebnis erfassen">
          <h3>Ergebnis des Anrufs</h3>
          {!canEdit ? <p className="cc-empty">Nur Ansicht: Ein anderes Fenster steuert gerade.</p> : (
            <>
              <label className="crm-note-field">Notiz zum Anruf
                <input ref={noteRef} value={note} maxLength={2000} placeholder="optional, wird mit dem Ergebnis gespeichert" onChange={(e) => setNote(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Escape") (e.target as HTMLInputElement).blur(); }} /></label>
              <div className="crm-outcomes" role="group" aria-label="Ergebnis wählen">
                {outcomes.map((d, i) => (
                  <button key={d.id} type="button" className={`cc-btn ${draft?.kind === d.id ? "pressed" : ""} ${d.id === "meeting_gebucht" ? "accent" : ""}`}
                    disabled={busy} aria-pressed={draft?.kind === d.id} onClick={() => choose(d.id)}>
                    {d.titel} <kbd>{i + 1}</kbd></button>
                ))}
              </div>
              {draft?.kind === "follow_up" && (
                <form className="crm-draft" aria-label="Follow-up terminieren" onSubmit={(e) => { e.preventDefault(); void record({ status: "follow_up", followup_am: draft.date, naechster_schritt: draft.step }); }}>
                  <label>Follow-up am<input type="date" required min={localDate()} value={draft.date} autoFocus onChange={(e) => setDraft({ ...draft, date: e.target.value })} /></label>
                  <label>Aufgabe (optional)<input value={draft.step} maxLength={300} placeholder="z. B. Angebot nachfassen" onChange={(e) => setDraft({ ...draft, step: e.target.value })} /></label>
                  <div className="cc-actions"><button type="submit" className="cc-btn small accent" disabled={busy}>Follow-up speichern</button>
                    <button type="button" className="cc-btn small" onClick={() => setDraft(null)}>Abbrechen</button></div>
                </form>
              )}
              {draft?.kind === "meeting_gebucht" && (
                <form className="crm-draft" aria-label="Meeting terminieren" onSubmit={(e) => { e.preventDefault(); void record({ status: "meeting_gebucht", termin_beginn: draft.start, kalender: draft.calendar }); }}>
                  <label>Meeting am<input type="datetime-local" required value={draft.start} autoFocus onChange={(e) => setDraft({ ...draft, start: e.target.value })} /></label>
                  <label className="crm-check"><input type="checkbox" checked={draft.calendar} onChange={(e) => setDraft({ ...draft, calendar: e.target.checked })} />
                    Kalender-Vorlage (30 Min., Eintrag erst nach Freigabe)</label>
                  <div className="cc-actions"><button type="submit" className="cc-btn small accent" disabled={busy}>Meeting speichern</button>
                    <button type="button" className="cc-btn small" onClick={() => setDraft(null)}>Abbrechen</button></div>
                </form>
              )}
              <div className="cc-actions">
                <button type="button" className="cc-btn small" disabled={busy || !note.trim()} onClick={() => void saveNote()}>Nur Notiz speichern</button>
                {busy && <span className="cc-hint" role="status">Speichert …</span>}
              </div>
            </>
          )}
          {error && <p className="ac-err" role="alert">{error}</p>}
          {calendarInfo && (
            <p className="crm-cal" role="status">{calendarInfo.hinweis}{" "}
              {calendarInfo.status === "vorlage" && <button type="button" className="cc-link" onClick={onOpenApprovals}>Zur Freigabe</button>}</p>
          )}
          <p className="cc-source" title={session ? "Nach dem Speichern springt die Session zum nächsten Lead." : undefined}>Jarvis wählt nicht selbst – die Nummer ist ein Link.</p>
          <h3 className="crm-history-head">Verlauf</h3>
          {acts == null ? <p className="cc-empty">Lädt …</p> : acts.length === 0 ? <p className="cc-empty">Noch keine Aktivität.</p> : (
            <ol className="crm-history">
              {acts.slice(0, 8).map((a) => (
                <li key={a.id}><span className="mono">{when(a.zeit)}</span>
                  <span>{a.art === "notiz" ? "Notiz" : <LeadStatus status={a.status ?? ""} defs={defs} />}{a.notiz ? ` ${a.notiz}` : ""}
                    {a.followup_am ? ` · Follow-up ${dateDe(a.followup_am)}` : ""}{a.termin_beginn ? ` · Meeting ${dateTimeDe(a.termin_beginn)}` : ""}</span></li>
              ))}
            </ol>
          )}
        </section>
      </div>
    </article>
  );
}

/** Cold-Call-CRM: Leads mit Status, fokussierte Call-Session, Auswertung. Alle Daten liegen lokal bei Jarvis. */
export function CrmPanel({ canEdit, auswertung, onOpenApprovals, focusLead }: {
  canEdit: boolean; auswertung: ReactNode; onOpenApprovals: () => void; focusLead?: string | null;
}) {
  const [tab, setTab] = useState<Tab>("leads");
  const [data, setData] = useState<LeadOverview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");
  const [query, setQuery] = useState("");
  // Seiten über den vollständigen Bestand: Der Server friert die Reihenfolge je Filter/Suche in einem Schnappschuss ein.
  // `page` ist der sichtbare Ausschnitt; Pfeile und Call-Session laufen über Seitengrenzen hinweg.
  const [page, setPage] = useState<{ snapshot: string; offset: number; total: number; ids: string[] }>({ snapshot: "", offset: 0, total: 0, ids: [] });
  const pageRef = useRef(page);
  pageRef.current = page;
  const [selectedId, setSelectedId] = useState<string | null>(focusLead ?? null);
  const [panel, setPanel] = useState<"neu" | "csv" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [sessionStart, setSessionStart] = useState<number | null>(loadSession);
  const [stats, setStats] = useState<LeadSession | null>(null);
  const [now, setNow] = useState(() => Date.now() / 1000);
  const [turning, setTurning] = useState(false);
  const alive = useRef(true);
  const reorder = useRef(true);
  const around = useRef<string | null>(focusLead ?? null);   // Seite so wählen, dass dieser Lead darauf liegt
  useEffect(() => () => void (alive.current = false), []);

  const activeFilter = tab === "session" ? "offen" : filter;
  const load = useCallback(async (go?: { offset: number; select?: "first" | "last" }) => {
    const fresh = reorder.current;
    const um = around.current ?? "";
    try {
      if (go) setTurning(true);
      const next = await api.leads(activeFilter, tab === "session" ? "" : query, {
        offset: go ? go.offset : fresh ? 0 : pageRef.current.offset, limit: PAGE_SIZE, snapshot: fresh ? "" : pageRef.current.snapshot, um });
      if (!alive.current) return;
      if (!Array.isArray(next?.leads) || !Array.isArray(next?.status)) throw new Error("Unerwartete Antwort vom Lead-Speicher.");
      reorder.current = false; around.current = null;
      setData(next); setError(null);
      setPage({ snapshot: next.snapshot ?? "", offset: next.offset ?? 0, total: next.treffer ?? next.leads.length, ids: next.leads.map((l) => l.id) });
      if (go?.select) setSelectedId(next.leads[go.select === "first" ? 0 : next.leads.length - 1]?.id ?? null);
    } catch (e) {
      if (alive.current) setError(e instanceof ApiError && e.status === 404
        ? "Der Jarvis-Server ist älter als dieses Dashboard (/api/leads fehlt) – Jarvis neu starten."
        : e instanceof Error ? e.message : "Leads nicht erreichbar.");
    } finally { if (alive.current) setTurning(false); }
  }, [activeFilter, query, tab]);
  useEffect(() => { reorder.current = true; const t = window.setTimeout(() => void load(), query ? 250 : 0); return () => window.clearTimeout(t); }, [load, query]);
  useEffect(() => { if (focusLead) { around.current = focusLead; setSelectedId(focusLead); setTab("leads"); } }, [focusLead]);

  const byId = useMemo(() => new Map((data?.leads ?? []).map((l) => [l.id, l])), [data]);
  const [extra, setExtra] = useState<Record<string, Lead>>({});
  // Der neuere Stand gewinnt: lokal Gespeichertes nur, bis der Server dieselbe oder eine höhere Version liefert
  // (z. B. nach einer Änderung in einem anderen Fenster).
  const leadOf = (id: string | null): Lead | null => {
    if (!id) return null;
    const local = extra[id], remote = byId.get(id);
    return local && remote ? (remote.version >= local.version ? remote : local) : local ?? remote ?? null;
  };
  const ids = page.ids.filter((id) => extra[id] || byId.has(id));
  const total = Math.max(page.total, ids.length);
  const pages = total > ids.length || page.offset > 0;
  const current = leadOf(selectedId) ?? (tab === "session" ? leadOf(ids[0] ?? null) : null);
  const index = current ? ids.indexOf(current.id) : -1;
  const lastOffset = Math.floor(Math.max(0, total - 1) / PAGE_SIZE) * PAGE_SIZE;
  /** Ein Schritt in der gefilterten Reihenfolge – am Seitenrand wird die Nachbarseite geladen, am Ende geht es von vorn weiter. */
  const step = (delta: 1 | -1) => {
    if (!ids.length || turning) return;
    const target = (index < 0 ? 0 : index) + delta;
    if (target >= 0 && target < ids.length) { setSelectedId(ids[target]); return; }
    if (!pages) { setSelectedId(ids[(target + ids.length) % ids.length]); return; }
    if (delta > 0) void load({ offset: page.offset + PAGE_SIZE > lastOffset ? 0 : page.offset + PAGE_SIZE, select: "first" });
    else void load({ offset: page.offset - PAGE_SIZE < 0 ? lastOffset : page.offset - PAGE_SIZE, select: "last" });
  };
  const turn = (offset: number) => { setSelectedId(null); void load({ offset }); };
  const defs = data?.status ?? [];

  // Session: Uhr und gemessene Calls (nur gespeicherte Ergebnisse zählen).
  const loadStats = useCallback(async () => {
    if (sessionStart == null) return;
    try { const s = await api.leadSession(sessionStart); if (alive.current) setStats(s); } catch { /* letzter Stand bleibt */ }
  }, [sessionStart]);
  useEffect(() => {
    if (sessionStart == null) { setStats(null); return; }
    void loadStats();
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(t);
  }, [sessionStart, loadStats]);
  const startSession = () => {
    const start = Date.now() / 1000;
    try { localStorage.setItem(SESSION_KEY, JSON.stringify({ start })); } catch { /* Session läuft dann nur bis zum Neuladen */ }
    reorder.current = true; setSessionStart(start); setSelectedId(null); setNow(start); setTab("session"); void load();
  };
  const endSession = () => { try { localStorage.removeItem(SESSION_KEY); } catch { /* egal */ } setSessionStart(null); };

  const saved = (lead: Lead, message: string) => {
    setExtra((old) => ({ ...old, [lead.id]: lead }));
    setNotice(message);
    void loadStats();
    if (tab !== "session") { void load(); return; }
    // Session: weiter zum nächsten Lead der eingefrorenen Reihenfolge – am Seitenende auf der nächsten Seite.
    const at = Math.max(0, ids.indexOf(lead.id));
    if (at + 1 < ids.length) { setSelectedId(ids[at + 1]); void load(); }
    else if (pages) void load({ offset: page.offset + PAGE_SIZE > lastOffset ? 0 : page.offset + PAGE_SIZE, select: "first" });
    else { if (ids.length > 1) setSelectedId(ids[0]); void load(); }
  };
  const changed = (lead: Lead) => { setExtra((old) => ({ ...old, [lead.id]: lead })); void load(); };
  // Neuer Lead: Reihenfolge neu aufbauen und die Seite zeigen, auf der er liegt. Das Formular meldet den Abschluss selbst.
  const created = (lead: Lead) => { setExtra((old) => ({ ...old, [lead.id]: lead })); reorder.current = true; around.current = lead.id; void load(); };
  const openLead = (id: string) => {
    setPanel(null); setSelectedId(id); around.current = id; reorder.current = true;
    if (filter || query) { setFilter(""); setQuery(""); } else void load();
  };

  const elapsed = sessionStart != null ? Math.max(0, now - sessionStart) : 0;
  const calls = stats?.calls ?? 0;
  const pace = elapsed >= 60 && calls > 0 ? Math.round((calls / elapsed) * 3600) : null;
  const nextUp = tab === "session" && current && index + 1 < ids.length ? leadOf(ids[index + 1]) : tab === "session" && current && !pages && ids.length > 1 ? leadOf(ids[0]) : null;
  const position = page.offset + index + 1;
  const chips: { id: string; label: string; count: number }[] = data ? [
    { id: "", label: "Alle", count: data.gesamt }, { id: "offen", label: "Anrufliste", count: data.offen },
    ...data.status.map((d) => ({ id: d.id, label: d.titel, count: data.zahlen[d.id] ?? 0 })),
  ] : [];

  return (
    <div className="crm">
      <div className="sp-head">
        <div className="cc-tabs" role="group" aria-label="CRM-Ansicht">
          <button type="button" aria-pressed={tab === "leads"} onClick={() => setTab("leads")}>Leads</button>
          <button type="button" aria-pressed={tab === "session"} onClick={() => { reorder.current = true; setTab("session"); }}>Aufgaben starten{sessionStart != null ? " · läuft" : ""}</button>
          <button type="button" aria-pressed={tab === "auswertung"} onClick={() => setTab("auswertung")}>Auswertung</button>
        </div>
        {tab === "leads" && canEdit && (
          <div className="cc-actions">
            <button type="button" className="cc-btn small" aria-expanded={panel === "csv"} onClick={() => setPanel(panel === "csv" ? null : "csv")}>CSV importieren</button>
            <button type="button" className="cc-btn small accent" aria-expanded={panel === "neu"} onClick={() => setPanel(panel === "neu" ? null : "neu")}><Icon name="plus" /> Lead anlegen</button>
          </div>
        )}
      </div>
      {notice && <p className="crm-ok" role="status">{notice} <button type="button" className="cc-link" onClick={() => setNotice(null)}>OK</button></p>}
      {error && <p className="cc-error" role="alert">{error} <button type="button" className="cc-link" onClick={() => void load()}>Erneut versuchen</button></p>}

      {tab === "auswertung" && auswertung}

      {tab === "leads" && (
        <>
          {panel === "neu" && <NewLead onCreated={created} onOpen={openLead} onCancel={() => setPanel(null)} />}
          {panel === "csv" && data && <CsvImport columns={data.csv_spalten} maxBytes={data.csv_max_bytes ?? 1_000_000} maxRows={data.csv_max_zeilen ?? 2000} onCancel={() => setPanel(null)}
            onDone={(count) => { setPanel(null); reorder.current = true; setNotice(`${count} Leads importiert.`); void load(); }} />}
          {!data && !error ? <ul className="td-skeleton" aria-label="Leads werden geladen"><li /><li /><li /></ul> : data && (
            <>
              <div className="crm-filter" role="group" aria-label="Nach Status filtern">
                {chips.map((c) => (
                  <button key={c.id || "alle"} type="button" aria-pressed={filter === c.id} onClick={() => { setFilter(c.id); setSelectedId(null); }}>
                    {c.label} <span className="mono">{c.count}</span></button>
                ))}
                <input type="search" className="cc-search" placeholder="Firma, Name, Telefon, Ort …" aria-label="Leads durchsuchen" value={query} onChange={(e) => setQuery(e.target.value)} />
              </div>
              {data.followups_faellig > 0 && filter !== "follow_up" && (
                <p className="crm-due" role="status">{data.followups_faellig} {data.followups_faellig === 1 ? "Follow-up ist" : "Follow-ups sind"} fällig.{" "}
                  <button type="button" className="cc-link" onClick={() => setFilter("follow_up")}>Anzeigen</button></p>
              )}
              <div className={`crm-body ${current ? "with-detail" : ""}`}>
                <section aria-label="Leads">
                  {data.gesamt === 0 ? (
                    <div className="crm-empty"><h3>Noch keine Leads</h3>
                      <p>Anlegen oder als CSV importieren.</p>
                      {canEdit && <button type="button" className="cc-btn brand" onClick={() => setPanel("neu")}>Ersten Lead anlegen</button>}</div>
                  ) : ids.length === 0 ? <p className="cc-empty">Kein Lead passt zu Filter und Suche.</p> : (
                    <table className="cc-table crm-table">
                      <thead><tr><th scope="col">Lead</th><th scope="col">Status</th><th scope="col">Nächster Schritt</th><th scope="col">Letzte Aktivität</th></tr></thead>
                      <tbody>
                        {ids.map((id) => { const l = leadOf(id)!; return (
                          <tr key={id} className={current?.id === id ? "selected" : ""}>
                            <td><button type="button" className="crm-open" aria-pressed={current?.id === id} onClick={() => setSelectedId(current?.id === id ? null : id)}>
                              <strong>{l.firma || l.name}</strong>{l.firma && l.name ? <span>{l.name}{l.rolle ? `, ${l.rolle}` : ""}</span> : null}</button></td>
                            <td><LeadStatus status={l.status} defs={defs} /></td>
                            <td>{l.status === "follow_up" && l.followup_am
                              ? <span className={l.followup_am <= data.heute ? "crm-overdue" : ""}>{l.naechster_schritt} · {dateDe(l.followup_am)}{l.followup_am <= data.heute ? " (fällig)" : ""}</span>
                              : l.termin_beginn ? `Meeting ${dateTimeDe(l.termin_beginn)}` : l.naechster_schritt || "–"}</td>
                            <td className="mono">{l.letzte_aktivitaet ? when(l.letzte_aktivitaet) : "–"}</td>
                          </tr>
                        ); })}
                      </tbody>
                    </table>
                  )}
                  {pages && (
                    <nav className="crm-pager" aria-label="Seiten der Leadliste">
                      <button type="button" className="cc-btn small" disabled={turning || page.offset === 0} onClick={() => turn(Math.max(0, page.offset - PAGE_SIZE))}>
                        <Icon name="arrowLeft" /> Zurück</button>
                      <span className="mono" role="status" aria-live="polite">{(page.offset + 1).toLocaleString("de-DE")}–{(page.offset + ids.length).toLocaleString("de-DE")} von {total.toLocaleString("de-DE")}</span>
                      <button type="button" className="cc-btn small" disabled={turning || page.offset + ids.length >= total} onClick={() => turn(page.offset + PAGE_SIZE)}>
                        Weiter <Icon name="arrowRight" /></button>
                    </nav>
                  )}
                </section>
                {current && <LeadDetail lead={current} defs={defs} position={index < 0 ? 0 : position} total={total} canEdit={canEdit} session={false}
                  onPrev={() => step(-1)} onNext={() => step(1)} onSaved={saved} onChanged={changed} onOpenApprovals={onOpenApprovals} />}
              </div>
            </>
          )}
        </>
      )}

      {tab === "session" && (
        sessionStart == null ? (
          <section className="crm-start" aria-label="Call-Session starten">
            <h2>Call-Session</h2>
            <p>Ein Lead nach dem anderen. Ergebnis per Klick oder Zifferntaste, dann sofort der nächste. Richtwert: {SESSION_TARGET} Calls pro Stunde.</p>
            <p className="crm-start-facts"><strong className="mono">{data?.offen ?? "–"}</strong> Leads auf der Anrufliste
              {data && data.followups_faellig > 0 ? <> · <strong className="mono">{data.followups_faellig}</strong> fällige Follow-ups zuerst</> : null}</p>
            <div className="cc-actions">
              <button type="button" className="cc-btn brand" disabled={!canEdit || !data || data.offen === 0} onClick={startSession}><Icon name="play" /> Session starten</button>
              {data && data.offen === 0 && <span className="cc-hint">Keine offenen Leads.</span>}
              {!canEdit && <span className="cc-hint">Nur Ansicht: Ein anderes Fenster steuert.</span>}
            </div>
          </section>
        ) : (
          <>
            <section className="crm-meter" aria-label="Fortschritt der Call-Session">
              <div className="crm-meter-num" aria-label={`${calls} von ${SESSION_TARGET} Calls`}>{calls}<em>/{SESSION_TARGET}</em></div>
              <div className="crm-meter-text">
                <strong>Session läuft</strong>
                <span className="mono" role="timer" aria-label="Laufzeit">{clockOf(elapsed)} min{pace != null ? ` · Tempo ${pace} pro Stunde` : " · Tempo ab der ersten Minute"}</span>
              </div>
              <div className="crm-meter-bar" role="progressbar" aria-valuemin={0} aria-valuemax={SESSION_TARGET} aria-valuenow={Math.min(calls, SESSION_TARGET)} aria-label="Calls in dieser Session">
                <i style={{ width: `${Math.min(100, (calls / SESSION_TARGET) * 100)}%` }} /></div>
              <div className="crm-meter-next">{nextUp ? <>Danach: <strong>{leadLabel(nextUp)}</strong></> : total <= 1 ? "Letzter Lead der Liste" : pages ? "Danach: nächste Seite" : ""}</div>
              <button type="button" className="cc-btn small" onClick={endSession}>Session beenden</button>
            </section>
            {!data && !error ? <ul className="td-skeleton" aria-hidden="true"><li /><li /></ul>
              : current ? <LeadDetail lead={current} defs={defs} position={index < 0 ? 0 : position} total={total} canEdit={canEdit} session
                  onPrev={() => step(-1)} onNext={() => step(1)} onSaved={saved} onChanged={changed} onOpenApprovals={onOpenApprovals} />
              : <div className="crm-empty"><h3>Anrufliste ist leer</h3><p>Alle offenen Leads sind bearbeitet. In dieser Session: {calls} erfasste Calls.</p>
                  <button type="button" className="cc-btn" onClick={endSession}>Session beenden</button></div>}
          </>
        )
      )}
    </div>
  );
}
