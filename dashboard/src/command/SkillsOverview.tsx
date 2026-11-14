import { useState } from "react";
import type { Automations, CapabilityRun, Platform, SkillDefinition } from "./api";

const RUN_LABEL: Record<string, string> = { RUNNING: "Läuft", PLANNED: "Geplant", WAITING_FOR_APPROVAL: "Benötigt Freigabe" };
const ACTIVE_RUN = (r: CapabilityRun) => r.status === "RUNNING" || r.status === "PLANNED" || r.status === "WAITING_FOR_APPROVAL";
const when = (ts: number) => new Date(ts * 1000).toLocaleString("de-DE", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
const LAST: Record<string, string> = { SUCCEEDED: "zuletzt erfolgreich", FAILED: "zuletzt fehlgeschlagen", UNCLEAR: "letzter Lauf unklar", STARTED: "läuft gerade" };

function SkillRow({ skill, canEdit, onStart, onToggle }: {
  skill: SkillDefinition; canEdit: boolean; onStart: (skill: string, task: string) => Promise<void>; onToggle: (id: string, active: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [task, setTask] = useState("");
  const [state, setState] = useState<string | null>(null);
  const start = async () => {
    if (task.trim().length < 8) { setState("Beschreibe kurz, was erledigt werden soll."); return; }
    setState("Startet …");
    try { await onStart(skill.id, task.trim()); setTask(""); setOpen(false); setState("Als Job gestartet – Fortschritt unter „Agenten / Jobs“."); }
    catch (e) { setState(e instanceof Error ? e.message : "Start fehlgeschlagen."); }
  };
  return (
    <li className="so-row">
      <span className="cc-state bereit">Verfügbar</span>
      <div className="so-main"><strong>{skill.name}</strong><span>{skill.beschreibung.slice(0, 140)}</span>
        {open && (
          <form className="so-start" onSubmit={(e) => { e.preventDefault(); void start(); }}>
            <label htmlFor={`so-${skill.id}`}>Auftrag für „{skill.name}“</label>
            <input id={`so-${skill.id}`} value={task} maxLength={1500} autoFocus onChange={(e) => setTask(e.target.value)} placeholder="Was soll erledigt werden?" />
            <div className="cc-actions"><button type="submit" className="cc-btn small accent">Als Job starten</button>
              <button type="button" className="cc-btn small" onClick={() => setOpen(false)}>Abbrechen</button></div>
          </form>
        )}
        {state && <span role="status">{state}</span>}
      </div>
      {canEdit && !open && (
        <div className="cc-actions">
          <button type="button" className="cc-btn small" onClick={() => { setOpen(true); setState(null); }} aria-label={`${skill.name} starten`}>Starten</button>
          <button type="button" className="cc-link" onClick={() => onToggle(skill.id, false)} aria-label={`${skill.name} deaktivieren`}>Deaktivieren</button>
        </div>
      )}
    </li>
  );
}

/** Skills und Automationen: nur was registriert und aktiv ist bzw. gerade läuft oder ansteht.
 *  Abgeschlossenes und Abgeschaltetes steht unter „Agenten / Jobs“ und im Systemprotokoll, nicht hier. */
export function SkillsOverview({ platform, platformError, automations, automationError, canEdit, busy, onStartSkill, onToggleSkill, onRunAction, onOpenApprovals, onOpenJobs }: {
  platform: Platform | null; platformError: string | null; automations: Automations | null; automationError: string | null;
  canEdit: boolean; busy: string | null; onStartSkill: (skill: string, task: string) => Promise<void>;
  onToggleSkill: (id: string, active: boolean) => void; onRunAction: (id: string, action: "abbrechen" | "wiederholen") => void;
  onOpenApprovals: () => void; onOpenJobs: () => void;
}) {
  const skills = (platform?.skills ?? []).filter((s) => s.aktiviert);
  const inactive = (platform?.skills ?? []).filter((s) => !s.aktiviert);
  const abilities = (platform?.faehigkeiten ?? []).filter((c) => c.aktiviert !== false);
  const runs = (platform?.laeufe ?? []).filter(ACTIVE_RUN);
  const flows = (automations?.ablaeufe ?? []).filter((w) => w.aktiv);
  const flowsOff = (automations?.ablaeufe ?? []).length - flows.length;
  const n8n = automations?.n8n.status === "bereit" ? automations.n8n.workflows : [];
  const titles = Object.fromEntries((platform?.faehigkeiten ?? []).map((c) => [c.name, c.titel]));
  const nothingScheduled = runs.length === 0 && flows.length === 0 && n8n.length === 0;
  return (
    <div className="so">
      <section aria-label="Verfügbare Skills">
        <header><h3>Verfügbare Skills</h3>{platform && <span className="cc-tag">{skills.length + abilities.length} aktiv</span>}</header>
        {!platform ? <p className="cc-empty">{platformError ?? "Lädt …"}</p> : skills.length + abilities.length === 0 ? (
          <p className="cc-empty">Kein Skill ist aktiv. Aktiviere unten einen registrierten Skill.</p>
        ) : (
          <ul className="so-list">
            {skills.map((s) => <SkillRow key={s.id} skill={s} canEdit={canEdit} onStart={onStartSkill} onToggle={onToggleSkill} />)}
            {abilities.map((c) => (
              <li key={c.name} className="so-row">
                <span className="cc-state bereit">Verfügbar</span>
                <div className="so-main"><strong>{c.titel}</strong><span>{c.beschreibung.slice(0, 140)}</span></div>
                <span className="cc-tag">{c.berechtigung_text || c.berechtigung}</span>
              </li>
            ))}
          </ul>
        )}
        {inactive.length > 0 && (
          <details className="so-more">
            <summary>{inactive.length} registrierte, aber nicht aktive Skills</summary>
            <ul className="so-list">
              {inactive.map((s) => (
                <li key={s.id} className="so-row">
                  <span className="cc-state aus">Nicht aktiv</span>
                  <div className="so-main"><strong>{s.name}</strong><span>{s.sperrgrund || s.beschreibung.slice(0, 140)}</span></div>
                  {canEdit && <button type="button" className="cc-btn small" onClick={() => onToggleSkill(s.id, true)} aria-label={`${s.name} aktivieren`}>Aktivieren</button>}
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>

      <section aria-label="Aktive und anstehende Automationen">
        <header><h3>Läuft und steht an</h3>
          {automations && <span className={`cc-state ${automations.ablaeufe_schleife === "läuft" ? "bereit" : "aus"}`}>Zeitplaner {automations.ablaeufe_schleife === "läuft" ? "läuft" : "aus"}</span>}</header>
        {!automations && !platform ? <p className="cc-empty">{automationError ?? "Lädt …"}</p> : nothingScheduled ? (
          <p className="cc-empty">Nichts läuft, nichts steht an. <span className="cc-mono" title="Abläufe schaltest du in dieser Datei ein.">~/.jarvis/ablaeufe.json</span></p>
        ) : (
          <ul className="so-list">
            {runs.map((r) => (
              <li key={r.id} className="so-row">
                <span className={`cc-state ${r.status}`}>{RUN_LABEL[r.status]}</span>
                <div className="so-main"><strong>{titles[r.faehigkeit] ?? r.faehigkeit}</strong>
                  <span>{r.fortschritt || `gestartet ${when(r.start)}`}</span></div>
                {r.status === "WAITING_FOR_APPROVAL" ? <button type="button" className="cc-btn small accent" onClick={onOpenApprovals}>Freigabe prüfen</button>
                  : canEdit && r.abbrechbar ? <button type="button" className="cc-btn small" disabled={busy === r.id} onClick={() => onRunAction(r.id, "abbrechen")}>Abbrechen</button> : null}
              </li>
            ))}
            {flows.map((w) => (
              <li key={w.id} className="so-row">
                <span className={`cc-state ${automations?.ablaeufe_schleife === "läuft" ? "PLANNED" : "aus"}`}>{automations?.ablaeufe_schleife === "läuft" ? "Steht an" : "Wartet auf Zeitplaner"}</span>
                <div className="so-main"><strong>{w.name}</strong>
                  <span>{w.zeitplan || "ohne festen Zeitplan"}{w.letzter_lauf ? ` · ${LAST[w.letzter_lauf.status] ?? w.letzter_lauf.status} ${when(w.letzter_lauf.zeit)}` : " · noch nie gelaufen"}</span></div>
                {w.letzter_lauf?.status === "FAILED" && <button type="button" className="cc-btn small" onClick={onOpenJobs}>Fehler ansehen</button>}
              </li>
            ))}
            {n8n.map((w) => (
              <li key={w.name} className="so-row">
                <span className="cc-state bereit">Bereit</span>
                <div className="so-main"><strong>{w.name}</strong><span>n8n-Workflow · {w.beschreibung || "ohne Beschreibung"}</span></div>
                <span className="cc-tag">{w.freigabe ? "startet nur mit Freigabe" : "ohne Freigabe"}</span>
              </li>
            ))}
          </ul>
        )}
        {automations && automations.ablaeufe_schleife !== "läuft" && flows.length > 0 && (
          <p className="cc-hint">Zeitplaner aus: Geplante Abläufe starten erst, wenn er läuft.</p>
        )}
        <p className="cc-hint">{flowsOff > 0 ? `${flowsOff} abgeschaltete Abläufe sind hier ausgeblendet. ` : ""}Vergangene Läufe:{" "}
          <button type="button" className="cc-link" onClick={onOpenJobs}>Agenten / Jobs</button>.</p>
      </section>
    </div>
  );
}
