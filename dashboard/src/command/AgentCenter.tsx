import { useEffect, useMemo, useState } from "react";
import type { AgentCenterData, AgentJob, AgentRole } from "./api";

/** Status in Alltagssprache. „Fehlgeschlagen“ durch Neustart heißt „Unterbrochen“, damit die Ursache sofort klar ist. */
export function jobState(job: Pick<AgentJob, "status" | "unterbrochen_durch_neustart">): { key: string; label: string } {
  if (job.unterbrochen_durch_neustart) return { key: "UNTERBROCHEN", label: "Unterbrochen (Neustart)" };
  switch (job.status) {
    case "RUNNING": return { key: "RUNNING", label: "Läuft" };
    case "PLANNED": return { key: "PLANNED", label: "Geplant" };
    case "WAITING_FOR_APPROVAL": return { key: "WAITING_FOR_APPROVAL", label: "Benötigt Freigabe" };
    case "COMPLETED": return { key: "COMPLETED", label: "Abgeschlossen" };
    case "FAILED": return { key: "FAILED", label: "Fehlgeschlagen" };
    case "CANCELLED": return { key: "CANCELLED", label: "Abgebrochen" };
    default: return { key: job.status, label: job.status };
  }
}
export const isActive = (job: Pick<AgentJob, "status">) => ["RUNNING", "PLANNED", "WAITING_FOR_APPROVAL"].includes(job.status);
/** Gespräche sind keine Jobs. Maßgeblich ist `klasse` vom Server; ohne sie (älterer Server) entscheidet die Art.
 *  Ausschlussliste, keine Erlaubnisliste: unbekannte Arten bleiben als Job sichtbar. */
const CHAT_KINDS = new Set(["vordergrund", "echtzeit", "gespraech", "chat"]);
export const isJob = (job: Pick<AgentJob, "art" | "klasse">) => job.klasse ? job.klasse !== "gespraech" : !CHAT_KINDS.has(job.art || "vordergrund");
/** Wichtig = braucht Aufmerksamkeit oder arbeitet gerade. */
export const isImportant = (job: Pick<AgentJob, "status" | "unterbrochen_durch_neustart">) =>
  isActive(job) || job.status === "FAILED" || job.unterbrochen_durch_neustart;

const clock = (ts: number | null | undefined) =>
  ts ? new Date(ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "–";
export function duration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "–";
  if (seconds < 1) return "< 1 s";
  if (seconds < 60) return `${Math.round(seconds)} s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ${Math.round(seconds % 60)} s`;
  return `${Math.floor(seconds / 3600)} h ${Math.round((seconds % 3600) / 60)} min`;
}
const PERMISSION: Record<string, string> = { read: "liest", lesen: "liest", local: "lokal", lokal: "lokal", write: "schreibt", external: "extern", extern: "extern" };
const ROLE_STATE: Record<AgentRole["status"], string> = {
  bereit: "Bereit", teilweise: "Teilweise", fehlt: "Fehlt", deaktiviert: "Deaktiviert", nicht_eingerichtet: "Nicht eingerichtet",
};
type Filter = "wichtig" | "freigabe" | "problem" | "fertig" | "alle";
const FILTERS: { id: Filter; label: string; test: (j: AgentJob) => boolean }[] = [
  { id: "wichtig", label: "Wichtig", test: isImportant },
  { id: "freigabe", label: "Benötigt Freigabe", test: (j) => j.status === "WAITING_FOR_APPROVAL" },
  { id: "problem", label: "Fehlgeschlagen", test: (j) => j.status === "FAILED" || j.unterbrochen_durch_neustart },
  { id: "fertig", label: "Abgeschlossen", test: (j) => j.status === "COMPLETED" },
  { id: "alle", label: "Alle Jobs", test: () => true },
];
/** Die eine nächste Aktion je Zeile (null = nur ansehen). */
function nextAction(job: AgentJob): { label: string; kind: "freigabe" | "wiederholen" | "abbrechen" } | null {
  if (job.status === "WAITING_FOR_APPROVAL") return { label: "Freigabe prüfen", kind: "freigabe" };
  if ((job.status === "FAILED" || job.unterbrochen_durch_neustart) && job.wiederholbar) return { label: "Erneut starten", kind: "wiederholen" };
  if ((job.status === "RUNNING" || job.status === "PLANNED") && job.abbrechbar) return { label: "Abbrechen", kind: "abbrechen" };
  return null;
}

/** Laufzeit zählt nur bei laufenden Jobs weiter (eine Sekunde Takt, kein erfundener Fortschritt). */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(t);
  }, [active]);
  return now;
}

function Steps({ job }: { job: AgentJob }) {
  if (job.schritte.length === 0)
    return <p className="cc-empty">Keine Werkzeug-Schritte – direkt beantwortet.</p>;
  return (
    <ol className="ac-steps">
      {job.schritte.map((s) => (
        <li key={s.id} className={s.status}>
          <span className="ac-step-mark" aria-hidden="true" />
          <div>
            <div className="ac-step-head"><span className="cc-mono">{s.werkzeug}</span>
              <span className="cc-tag">{PERMISSION[s.berechtigung] ?? s.berechtigung}</span>
              <span className={`cc-state ${s.status}`}>{jobState({ status: s.status, unterbrochen_durch_neustart: s.fehler === "Neustart" }).label}</span>
              {s.start != null && <span className="ac-time">{s.ende != null ? duration(s.ende - s.start) : "läuft"}</span>}</div>
            {s.ergebnis && <p>{s.ergebnis}</p>}
            {s.fehler && s.fehler !== "Neustart" && <p className="ac-err">{s.fehler}</p>}
          </div>
        </li>
      ))}
    </ol>
  );
}

function JobDetail({ job, canAct, busy, onCancel, onRetry, onApprovals, onOpenOrder, onClose }: {
  job: AgentJob; canAct: boolean; busy: boolean; onCancel: (id: string) => void; onRetry: (id: string) => void;
  onApprovals: () => void; onOpenOrder: (id: string) => void; onClose: () => void;
}) {
  const now = useNow(isActive(job));
  const state = jobState(job);
  const orderId = /\bA-[0-9a-f]{6,}\b/.exec(job.ergebnis ?? "")?.[0];
  const next: [string, string] | null = job.status === "WAITING_FOR_APPROVAL" ? ["Vorlage wartet auf deine Entscheidung.", "Erst danach wird außerhalb von Jarvis etwas geändert."]
    : job.unterbrochen_durch_neustart ? ["Läuft nicht von selbst weiter. Ergebnis im Zielsystem prüfen, dann neu beauftragen.", "Jarvis wurde während der Ausführung beendet."]
    : job.status === "FAILED" ? (job.wiederholbar ? ["Mit denselben Eingaben neu starten.", ""] : ["Bitte neu beauftragen.", "Für diesen Job sind keine Eingaben zur Wiederholung gespeichert."])
    : job.status === "CANCELLED" ? ["Abgebrochen. Nichts wird doppelt ausgeführt.", "Bereits ausgeführte Schritte bleiben bestehen."]
    : job.status === "RUNNING" ? [job.fortschritt || "Der Agent arbeitet.", ""] : null;
  return (
    <aside className="cc-detail ac-detail" aria-label="Job-Details">
      <div className="ac-detail-head">
        <div><span className={`cc-state ${state.key}`}>{state.label}</span><h3>{job.titel}</h3></div>
        <button type="button" className="cc-btn small" onClick={onClose}>Schließen</button>
      </div>
      <dl>
        <dt>Agent</dt><dd>{job.agent.name} <span className="cc-tag">{job.agent.art}</span></dd>
        {job.unteragenten.length > 0 && <><dt>Hinzugezogen</dt><dd>{job.unteragenten.join(", ")}</dd></>}
        {job.faehigkeit && <><dt>Fähigkeit</dt><dd><span className="cc-mono">{job.faehigkeit}</span></dd></>}
        <dt>Gestartet</dt><dd>{clock(job.start)}</dd>
        <dt>Laufzeit</dt><dd>{duration((job.ende ?? now) - job.start)}{isActive(job) && job.status !== "WAITING_FOR_APPROVAL" ? " (läuft)" : ""}</dd>
        <dt>Fortschritt</dt><dd>{job.schritte_gesamt === 0 ? "keine Werkzeug-Schritte" : `${job.schritte_fertig} von ${job.schritte_gesamt} Schritten beendet`}</dd>
      </dl>
      <details className="crm-more"><summary>Technik: Werkzeuge, Modell, Nutzung</summary>
        <dl>
          <dt>Letzte Aktivität</dt><dd>{clock(job.letzte_aktivitaet)}</dd>
          {job.werkzeuge.length > 0 && <><dt>Werkzeuge</dt><dd>{job.werkzeuge.map((w) => <span key={w} className="cc-mono ac-tool">{w}</span>)}</dd></>}
          {job.modell && <><dt>Modell</dt><dd><span className="cc-mono">{job.modell}</span></dd></>}
          <dt>Nutzung</dt><dd>{job.nutzung
            ? <>{Object.values(job.nutzung.tokens).reduce((a, b) => a + b, 0).toLocaleString("de-DE")} Tokens{job.nutzung.kosten_usd != null ? ` · ${job.nutzung.kosten_usd.toFixed(4)} USD` : " · Kosten nicht erfasst"}</>
            : "nicht erfasst"}</dd>
          {job.repo && <><dt>Repository</dt><dd>{job.repo.name} <span className="cc-mono">{job.repo.commit.slice(0, 10)}</span></dd></>}
        </dl></details>
      {next && <p className="ac-next" title={next[1] || undefined}><strong>Nächster Schritt:</strong> {next[0]}</p>}
      {job.fehler && !job.unterbrochen_durch_neustart && <p className="ac-err"><strong>Fehlergrund:</strong> {job.fehler}</p>}
      {job.ergebnis && <div className="ac-result"><h4>Ergebnis</h4><p>{job.ergebnis}</p></div>}
      <div><h4>Schritte</h4><Steps job={job} /></div>
      <div className="cc-actions">
        {job.status === "WAITING_FOR_APPROVAL" && <button type="button" className="cc-btn small accent" onClick={onApprovals}>Freigabe prüfen</button>}
        {orderId && <button type="button" className="cc-btn small" onClick={() => onOpenOrder(orderId)}>Auftrag öffnen</button>}
        {canAct && job.abbrechbar && <button type="button" className="cc-btn small" disabled={busy} onClick={() => onCancel(job.id)}>Abbrechen</button>}
        {canAct && job.wiederholbar && <button type="button" className="cc-btn small" disabled={busy} onClick={() => onRetry(job.id)}>Erneut starten</button>}
      </div>
    </aside>
  );
}

function RoleCard({ role, canAct, onStart, onOpenJob }: {
  role: AgentRole; canAct: boolean; onStart: (role: AgentRole, task: string) => Promise<void>; onOpenJob: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [more, setMore] = useState(false);
  const [task, setTask] = useState(role.vorlage);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submit = async () => {
    if (task.trim().length < 8) { setError("Beschreibe kurz, was erledigt werden soll."); return; }
    setBusy(true); setError(null);
    try { await onStart(role, task.trim()); setOpen(false); setTask(role.vorlage); }
    catch (e) { setError(e instanceof Error ? e.message : "Start fehlgeschlagen."); }
    finally { setBusy(false); }
  };
  const tone = role.status === "bereit" ? "bereit" : role.status === "teilweise" ? "RUNNING" : "nicht_eingerichtet";
  return (
    <li className={`ac-role ${role.jobs_aktiv > 0 ? "busy" : ""}`}>
      <div className="ac-role-row">
        <button type="button" className="ac-role-name" aria-expanded={more} onClick={() => setMore((v) => !v)} title="Aufgabe und Umsetzung zeigen">
          <strong>{role.name}</strong></button>
        <span className={`cc-state ${tone}`}>{ROLE_STATE[role.status]}</span>
        <span className="ac-role-load">{role.jobs_aktiv > 0
          ? <span className="ac-live"><i className="ac-dot RUNNING" aria-hidden="true" />{role.jobs_aktiv} aktiv</span>
          : role.letzter_job ? <button type="button" className="cc-link" onClick={() => onOpenJob(role.letzter_job!.id)} aria-label={`Letzter Job von ${role.name} öffnen`}>zuletzt {clock(role.letzter_job.start)}</button>
          : <span className="ac-idle">frei</span>}</span>
        {role.startbar && canAct && !open
          ? <button type="button" className="cc-btn small" onClick={() => setOpen(true)} aria-label={`Auftrag starten: ${role.name}`}>Auftrag starten</button>
          : <span className="ac-role-none" aria-hidden="true" />}
      </div>
      {more && <p className="ac-impl">{role.aufgabe} <span className="ac-how">{role.umsetzung}{role.grund ? ` · ${role.grund}` : ""}</span></p>}
      {!more && role.grund && role.status !== "bereit" && <p className="ac-impl">{role.grund}</p>}
      {open && (
        <div className="ac-start">
          <label htmlFor={`task-${role.id}`}>Auftrag für {role.name}</label>
          <textarea id={`task-${role.id}`} rows={3} value={task} onChange={(e) => setTask(e.target.value)} maxLength={1500} autoFocus />
          {error && <p className="ac-err" role="alert">{error}</p>}
          <div className="cc-actions">
            <button type="button" className="cc-btn small accent" disabled={busy} onClick={() => void submit()}>{busy ? "Startet …" : "Als Job starten"}</button>
            <button type="button" className="cc-btn small" onClick={() => setOpen(false)}>Abbrechen</button>
            <span className="cc-hint" title="Läuft als eigener Job mit dem Werkzeug-Rahmen der Skill.">Außenwirksames landet in „Freigaben“.</span>
          </div>
        </div>
      )}
    </li>
  );
}

/** Wichtige Jobs in der Reihenfolge, in der sie dich betreffen. */
const GROUPS: { id: string; label: string; test: (j: AgentJob) => boolean }[] = [
  { id: "du", label: "Braucht dich", test: (j) => j.status === "WAITING_FOR_APPROVAL" || j.status === "FAILED" || j.unterbrochen_durch_neustart },
  { id: "laeuft", label: "Läuft", test: (j) => j.status === "RUNNING" },
  { id: "geplant", label: "Geplant", test: (j) => j.status === "PLANNED" },
];

/** Agenten-Center: echte Jobs links, Details rechts, darunter die Rollen und womit sie tatsächlich umgesetzt sind. */
export function AgentCenter({ data, error, canAct, focusJob, busyId, onCancel, onRetry, onApprovals, onOpenOrder, onStartRole, onRefresh }: {
  data: AgentCenterData | null; error: string | null; canAct: boolean; focusJob: string | null; busyId: string | null;
  onCancel: (id: string) => void; onRetry: (id: string) => void; onApprovals: () => void; onOpenOrder: (id: string) => void;
  onStartRole: (role: AgentRole, task: string) => Promise<void>; onRefresh: () => void;
}) {
  // Jobs, keine Gespräche: Chat- und Echtzeit-Züge stehen im Gesprächsarchiv (Gedächtnis), nicht hier.
  const jobs = useMemo(() => (data?.jobs ?? []).filter(isJob), [data]);
  const chats = (data?.jobs.length ?? 0) - jobs.length;
  const anyActive = jobs.some(isActive);
  const [filter, setFilter] = useState<Filter>("wichtig");
  const [selectedId, setSelectedId] = useState<string | null>(focusJob);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => { if (focusJob) { setSelectedId(focusJob); setFilter("alle"); } }, [focusJob]);
  const now = useNow(anyActive);
  const shown = useMemo(() => jobs.filter(FILTERS.find((f) => f.id === filter)!.test), [jobs, filter]);
  const selected = jobs.find((j) => j.id === selectedId) ?? null;
  if (!data) return <p className="cc-empty">{error ?? "Lädt …"}</p>;
  const count = (f: Filter) => jobs.filter(FILTERS.find((x) => x.id === f)!.test).length;
  const visible = showAll ? shown : shown.slice(0, 8);
  // „Wichtig“ ist gruppiert (Braucht dich, Läuft, Geplant); die übrigen Ansichten sind eine einfache Liste.
  const groups = filter === "wichtig"
    ? GROUPS.map((g) => ({ ...g, jobs: visible.filter(g.test) })).filter((g) => g.jobs.length > 0)
    : [{ id: "alle", label: "", jobs: visible, test: () => true }];
  const row = (j: AgentJob) => {
    const st = jobState(j);
    const action = canAct || j.status === "WAITING_FOR_APPROVAL" ? nextAction(j) : null;
    return (
      <li key={j.id} className={`ac-job-row ${st.key}`}>
        <button type="button" className={`cc-row ac-job ${selectedId === j.id ? "selected" : ""}`} aria-pressed={selectedId === j.id}
          onClick={() => setSelectedId(selectedId === j.id ? null : j.id)}>
          <span className={`cc-state ${st.key}`}>{st.label}</span>
          <span className="grow"><span className="title">{j.titel}</span>
            <span className="meta"><span className="ac-who">{j.agent.name}</span> · {clock(j.start)} · {duration((j.ende ?? now) - j.start)}
              {j.schritte_gesamt > 0 && ` · ${j.schritte_fertig}/${j.schritte_gesamt} Schritte`}
              {j.fehler && !j.unterbrochen_durch_neustart ? ` · ${j.fehler.length > 70 ? j.fehler.slice(0, 70) + " …" : j.fehler}` : ""}</span></span>
        </button>
        {action ? (
          <button type="button" className={`cc-btn small ${action.kind === "freigabe" ? "brand" : ""}`} disabled={busyId === j.id}
            aria-label={`${action.label}: ${j.titel}`}
            onClick={() => action.kind === "freigabe" ? onApprovals() : action.kind === "wiederholen" ? onRetry(j.id) : onCancel(j.id)}>{action.label}</button>
        ) : <span className="ac-none">{isActive(j) ? "läuft selbst" : "nur ansehen"}</span>}
      </li>
    );
  };
  const busyRoles = data.rollen.filter((r) => r.jobs_aktiv > 0).length;
  return (
    <div className="ac">
      <div className="ac-summary" role="group" aria-label="Jobs nach Status">
        {FILTERS.map((f) => (
          <button key={f.id} type="button" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}>
            <strong>{count(f.id)}</strong><span>{f.label}</span>
          </button>
        ))}
      </div>
      <div className={`ac-body ${selected ? "with-detail" : ""}`}>
        <section className="cc-card-box" aria-label="Jobs">
          <header><h3>{FILTERS.find((f) => f.id === filter)!.label}</h3><button type="button" className="cc-link" onClick={onRefresh}>Aktualisieren</button></header>
          {shown.length === 0 ? (
            <p className="cc-empty">{jobs.length === 0 ? "Noch kein Job." : filter === "wichtig" ? "Nichts Wichtiges offen." : "Keine Jobs in dieser Ansicht."}</p>
          ) : (
            <>
              <div className="ac-cols" aria-hidden="true"><span>Status</span><span>Job · Rolle</span><span>Nächste Aktion</span></div>
              {groups.map((g) => (
                <div key={g.id} className={`ac-group ${g.id}`}>
                  {g.label && <h4><span>{g.label}</span><span className="mono">{g.jobs.length}</span></h4>}
                  <ul className="cc-list ac-jobs" aria-label={g.label || undefined}>{g.jobs.map(row)}</ul>
                </div>
              ))}
            </>
          )}
          {shown.length > 8 && <button type="button" className="cc-link" onClick={() => setShowAll(!showAll)}>{showAll ? "Weniger anzeigen" : `Alle ${shown.length} anzeigen`}</button>}
          {chats > 0 && <p className="cc-hint" title={data.hinweis}>{chats} Gesprächszüge sind hier ausgeblendet – sie stehen im Gesprächsarchiv unter „Gedächtnis“.</p>}
        </section>
        {selected && (
          <JobDetail job={selected} canAct={canAct} busy={busyId === selected.id} onCancel={onCancel} onRetry={onRetry}
            onApprovals={onApprovals} onOpenOrder={onOpenOrder} onClose={() => setSelectedId(null)} />
        )}
      </div>
      {data.rollen.length > 0 && (
        <section className="ac-roles" aria-label="Rollen">
          <header><h3>Rollen</h3><span className="cc-tag" title="Eine Rolle ist der Orchestrator plus Skill und Werkzeug-Rahmen, kein eigener Dauerläufer. Aktiv ist nur, wofür wirklich ein Job läuft.">
            {busyRoles} von {data.rollen.length} aktiv</span></header>
          <div className="ac-cols roles" aria-hidden="true"><span>Rolle</span><span>Einrichtung</span><span>Auslastung</span><span>Nächste Aktion</span></div>
          <ul className="ac-role-list">
            {data.rollen.map((r) => <RoleCard key={r.id} role={r} canAct={canAct} onStart={onStartRole} onOpenJob={(id) => { setSelectedId(id); setFilter("alle"); }} />)}
          </ul>
        </section>
      )}
      {data.agenten.length > 0 && (
        <details className="ac-cores">
          <summary><h3>Modelle und Kerne</h3><span className="cc-tag">{data.agenten.filter((a) => a.status === "bereit").length} von {data.agenten.length} bereit</span></summary>
          <ul className="ac-role-list">
            {data.agenten.map((a) => (
              <li key={a.id} className="ac-role"><div className="ac-role-row cores">
                <strong>{a.name}</strong>
                <span className={`cc-state ${a.status}`}>{a.status === "bereit" ? "Bereit" : a.status === "fehler" ? "Fehler" : "Nicht eingerichtet"}</span>
                <span className="cc-mono">{a.modell}</span></div>
                <p className="ac-impl">{a.rolle}{a.hinweis ? ` · ${a.hinweis}` : ""}</p></li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
