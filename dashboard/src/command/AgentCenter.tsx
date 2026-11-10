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
type Filter = "aktiv" | "freigabe" | "fertig" | "problem" | "alle";
const FILTERS: { id: Filter; label: string; test: (j: AgentJob) => boolean }[] = [
  { id: "aktiv", label: "Aktiv", test: (j) => j.status === "RUNNING" || j.status === "PLANNED" },
  { id: "freigabe", label: "Benötigt Freigabe", test: (j) => j.status === "WAITING_FOR_APPROVAL" },
  { id: "fertig", label: "Abgeschlossen", test: (j) => j.status === "COMPLETED" },
  { id: "problem", label: "Fehlgeschlagen / abgebrochen", test: (j) => j.status === "FAILED" || j.status === "CANCELLED" },
  { id: "alle", label: "Alle", test: () => true },
];

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
    return <p className="cc-empty">Keine Werkzeug-Schritte – der Agent hat direkt geantwortet{job.art === "echtzeit" ? " (Echtzeit-Gespräch; Schritte entstehen nur bei Werkzeugaufrufen)" : ""}.</p>;
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
  const next = job.status === "WAITING_FOR_APPROVAL" ? "Eine Vorlage wartet auf deine Entscheidung. Erst danach wird außerhalb von Jarvis etwas geändert."
    : job.unterbrochen_durch_neustart ? "Jarvis wurde während der Ausführung beendet. Der Job läuft nicht von selbst weiter – prüfe das Ergebnis im Zielsystem, bevor du neu beauftragst."
    : job.status === "FAILED" ? (job.wiederholbar ? "Du kannst den Lauf mit denselben Eingaben neu starten." : "Bitte neu beauftragen – für diesen Job sind keine Eingaben zur Wiederholung gespeichert.")
    : job.status === "CANCELLED" ? "Abgebrochen. Bereits ausgeführte Schritte bleiben bestehen; nichts wird doppelt ausgeführt."
    : job.status === "RUNNING" ? (job.fortschritt || "Der Agent arbeitet. Schritte erscheinen hier, sobald er Werkzeuge nutzt.") : "";
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
        <dt>Letzte Aktivität</dt><dd>{clock(job.letzte_aktivitaet)}</dd>
        <dt>Laufzeit</dt><dd>{duration((job.ende ?? now) - job.start)}{isActive(job) && job.status !== "WAITING_FOR_APPROVAL" ? " (läuft)" : ""}</dd>
        <dt>Fortschritt</dt><dd>{job.schritte_gesamt === 0 ? "keine Werkzeug-Schritte" : `${job.schritte_fertig} von ${job.schritte_gesamt} Schritten beendet`}</dd>
        {job.werkzeuge.length > 0 && <><dt>Werkzeuge</dt><dd>{job.werkzeuge.map((w) => <span key={w} className="cc-mono ac-tool">{w}</span>)}</dd></>}
        {job.modell && <><dt>Modell</dt><dd><span className="cc-mono">{job.modell}</span></dd></>}
        <dt>Nutzung</dt><dd>{job.nutzung
          ? <>{Object.values(job.nutzung.tokens).reduce((a, b) => a + b, 0).toLocaleString("de-DE")} Tokens{job.nutzung.kosten_usd != null ? ` · ${job.nutzung.kosten_usd.toFixed(4)} USD` : " · Kosten nicht erfasst (keine Preise konfiguriert)"}</>
          : "nicht erfasst"}</dd>
        {job.repo && <><dt>Repository</dt><dd>{job.repo.name} <span className="cc-mono">{job.repo.commit.slice(0, 10)}</span></dd></>}
      </dl>
      {next && <p className="ac-next"><strong>Nächster Schritt:</strong> {next}</p>}
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
  return (
    <article className="cc-card-box cc-agent ac-role">
      <div className="head"><strong>{role.name}</strong><span className={`cc-state ${role.status === "bereit" ? "bereit" : role.status === "teilweise" ? "RUNNING" : "nicht_eingerichtet"}`}>{ROLE_STATE[role.status]}</span></div>
      <p>{role.aufgabe}</p>
      <p className="ac-impl">{role.umsetzung}{role.grund ? ` · ${role.grund}` : ""}</p>
      <p className="ac-impl">{role.jobs_aktiv > 0 ? `${role.jobs_aktiv} Job${role.jobs_aktiv === 1 ? "" : "s"} aktiv` : "Kein aktiver Job"}
        {role.letzter_job && <> · zuletzt <button type="button" className="cc-link" onClick={() => onOpenJob(role.letzter_job!.id)}>{clock(role.letzter_job.start)}</button></>}</p>
      {open ? (
        <div className="ac-start">
          <label htmlFor={`task-${role.id}`}>Auftrag für diese Rolle</label>
          <textarea id={`task-${role.id}`} rows={3} value={task} onChange={(e) => setTask(e.target.value)} maxLength={1500} autoFocus />
          {error && <p className="ac-err">{error}</p>}
          <div className="cc-actions">
            <button type="button" className="cc-btn small accent" disabled={busy} onClick={() => void submit()}>{busy ? "Startet …" : "Als Job starten"}</button>
            <button type="button" className="cc-btn small" onClick={() => setOpen(false)}>Abbrechen</button>
          </div>
          <p className="cc-hint">Läuft als eigener Job mit dem Werkzeug-Rahmen der Skill. Außenwirksames landet als Vorlage in „Freigaben“.</p>
        </div>
      ) : role.startbar && canAct ? (
        <div><button type="button" className="cc-btn small" onClick={() => setOpen(true)}>Auftrag starten</button></div>
      ) : null}
    </article>
  );
}

/** Agenten-Center: echte Jobs links, Details rechts, darunter die Rollen und womit sie tatsächlich umgesetzt sind. */
export function AgentCenter({ data, error, canAct, focusJob, busyId, onCancel, onRetry, onApprovals, onOpenOrder, onStartRole, onRefresh }: {
  data: AgentCenterData | null; error: string | null; canAct: boolean; focusJob: string | null; busyId: string | null;
  onCancel: (id: string) => void; onRetry: (id: string) => void; onApprovals: () => void; onOpenOrder: (id: string) => void;
  onStartRole: (role: AgentRole, task: string) => Promise<void>; onRefresh: () => void;
}) {
  const jobs = data?.jobs ?? [];
  const anyActive = jobs.some(isActive);
  const [filter, setFilter] = useState<Filter>(anyActive ? "aktiv" : "alle");
  const [selectedId, setSelectedId] = useState<string | null>(focusJob);
  const [showAll, setShowAll] = useState(false);
  useEffect(() => { if (focusJob) { setSelectedId(focusJob); setFilter("alle"); } }, [focusJob]);
  const now = useNow(anyActive);
  const shown = useMemo(() => jobs.filter(FILTERS.find((f) => f.id === filter)!.test), [jobs, filter]);
  const selected = jobs.find((j) => j.id === selectedId) ?? null;
  if (!data) return <p className="cc-empty">{error ?? "Lädt …"}</p>;
  const count = (f: Filter) => jobs.filter(FILTERS.find((x) => x.id === f)!.test).length;
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
          <header><h3>Jobs</h3><button type="button" className="cc-link" onClick={onRefresh}>Aktualisieren</button></header>
          {shown.length === 0 ? (
            <p className="cc-empty">{jobs.length === 0 ? "Noch kein Job. Sobald du Jarvis etwas aufträgst oder unten eine Rolle startest, erscheint die Arbeit hier – mit echten Schritten."
              : filter === "aktiv" ? "Gerade läuft nichts. Kein Agent ist aktiv." : "Keine Jobs in dieser Ansicht."}</p>
          ) : (
            <ul className="cc-list">
              {(showAll ? shown : shown.slice(0, 8)).map((j) => {
                const st = jobState(j);
                return (
                  <li key={j.id}>
                    <button type="button" className={`cc-row ac-job ${selectedId === j.id ? "selected" : ""}`} aria-pressed={selectedId === j.id}
                      onClick={() => setSelectedId(selectedId === j.id ? null : j.id)}>
                      <span className={`ac-dot ${st.key}`} aria-hidden="true" />
                      <span className="grow"><span className="title">{j.titel}</span>
                        <span className="meta">{j.agent.name} · {clock(j.start)} · {duration((j.ende ?? now) - j.start)}
                          {j.schritte_gesamt > 0 && ` · ${j.schritte_fertig}/${j.schritte_gesamt} Schritte`}
                          {j.fehler && !j.unterbrochen_durch_neustart ? ` · ${j.fehler.length > 70 ? j.fehler.slice(0, 70) + " …" : j.fehler}` : ""}</span></span>
                      <span className={`cc-state ${st.key}`}>{st.label}</span>
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
          {shown.length > 8 && <button type="button" className="cc-link" onClick={() => setShowAll(!showAll)}>{showAll ? "Weniger anzeigen" : `Alle ${shown.length} anzeigen`}</button>}
          <p className="cc-hint">{data.hinweis}</p>
        </section>
        {selected && (
          <JobDetail job={selected} canAct={canAct} busy={busyId === selected.id} onCancel={onCancel} onRetry={onRetry}
            onApprovals={onApprovals} onOpenOrder={onOpenOrder} onClose={() => setSelectedId(null)} />
        )}
      </div>
      <div className="cc-page-head ac-roles-head"><div><h2>Rollen</h2>
        <p>Was Jarvis abdeckt und womit. Eine Rolle ist der Orchestrator plus Skill und Werkzeug-Rahmen – kein eigener Dauerläufer. „Aktiv“ ist nur, wofür wirklich ein Job läuft.</p></div></div>
      <div className="cc-grid">
        {data.rollen.map((r) => <RoleCard key={r.id} role={r} canAct={canAct} onStart={onStartRole} onOpenJob={(id) => { setSelectedId(id); setFilter("alle"); }} />)}
      </div>
      <div className="cc-page-head ac-roles-head"><div><h2>Modelle und Kerne</h2><p>Die KI-Bausteine hinter den Rollen, mit Einrichtungsstatus.</p></div></div>
      <div className="cc-grid">
        {data.agenten.map((a) => (
          <article key={a.id} className="cc-card-box cc-agent">
            <div className="head"><strong>{a.name}</strong><span className={`cc-state ${a.status}`}>{a.status === "bereit" ? "Bereit" : a.status === "fehler" ? "Fehler" : "Nicht eingerichtet"}</span></div>
            <p>{a.rolle}</p><div><span className="cc-mono">{a.modell}</span></div>{a.hinweis && <p>{a.hinweis}</p>}
          </article>
        ))}
      </div>
    </div>
  );
}
