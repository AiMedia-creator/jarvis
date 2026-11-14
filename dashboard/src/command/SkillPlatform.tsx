import { useState } from "react";
import type { CapabilityRun, GitHubSummary, Platform } from "./api";
import "./center.css";

const STATUS: Record<string, { label: string; tone: string }> = {
  PLANNED: { label: "wartend", tone: "wait" }, RUNNING: { label: "läuft", tone: "run" },
  WAITING_FOR_APPROVAL: { label: "benötigt Freigabe", tone: "wait" }, COMPLETED: { label: "abgeschlossen", tone: "ok" },
  FAILED: { label: "fehlgeschlagen", tone: "err" }, CANCELLED: { label: "abgebrochen", tone: "off" },
};
const PERM_LABEL: Record<string, string> = {
  lesen: "nur lesen", lokal: "lokal · ohne Freigabe", extern: "extern · nur mit Freigabe", code_ausfuehren: "Code ausführen",
  repo_schreiben: "Repo schreiben", push: "Push", merge: "Merge", veroeffentlichen: "Veröffentlichen",
};
const RAHMEN: Record<string, string> = {
  frei: "alle registrierten Werkzeuge (Freigabe-Regeln gelten)", deklariert: "nur deklarierte Werkzeuge", nur_lesen: "nur lesende Werkzeuge",
};
const SOURCE: Record<string, string> = { web: "Studio", echtzeit: "Echtzeit", hintergrund: "Hintergrund", dashboard: "Command Center", jarvis: "Jarvis" };
const clock = (s: number) => new Date(s * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
const duration = (r: CapabilityRun) => (r.ende ? `${Math.max(0, Math.round((r.ende - r.start) * 1000))} ms` : "");

export type PlatformProps = {
  platform: Platform | null; error: string | null; canEdit: boolean; busy: string | null;
  onAction: (id: string, action: "abbrechen" | "wiederholen") => void; onOpenOrder: (id: string) => void;
  onStart: (name: string, args: Record<string, unknown>) => Promise<void>; onToggleSkill: (id: string, active: boolean) => void;
  onOpenApprovals: () => void;
};

/** Skill-Plattform: was Jarvis wirklich ausführen kann, woher es stammt, was gerade läuft. */
export function SkillPlatform(props: PlatformProps) {
  const { platform, error } = props;
  const running = platform?.laeufe.some((r) => r.status === "RUNNING" || r.status === "PLANNED") ?? false;
  const [tab, setTab] = useState<"auftraege" | "bibliothek">("auftraege");
  if (error) return <section className="cc-sp"><h3>Skill-Plattform</h3><p className="cc-warn-text">{error}</p></section>;
  if (!platform) return <section className="cc-sp"><h3>Skill-Plattform</h3><p className="cc-empty">Wird geladen …</p></section>;
  return (
    <section className="cc-sp" aria-label="Skill-Plattform">
      <header className="cc-sp-head">
        <h3>Skill-Plattform</h3>
        <div className="cc-sp-tabs" role="tablist" aria-label="Ansicht">
          <button type="button" role="tab" aria-selected={tab === "auftraege"} className={tab === "auftraege" ? "on" : ""} onClick={() => setTab("auftraege")}>
            Aufträge{running ? <i className="cc-sp-live" aria-label="läuft" /> : null}
          </button>
          <button type="button" role="tab" aria-selected={tab === "bibliothek"} className={tab === "bibliothek" ? "on" : ""} onClick={() => setTab("bibliothek")}>
            Bibliothek <span className="cc-hint">{platform.faehigkeiten.length + platform.skills.length}</span>
          </button>
        </div>
      </header>
      {tab === "auftraege" ? <Jobs {...props} platform={platform} /> : <Library {...props} platform={platform} />}
    </section>
  );
}

function Jobs({ platform, canEdit, busy, onAction, onOpenOrder, onStart, onOpenApprovals }: PlatformProps & { platform: Platform }) {
  const [repo, setRepo] = useState("");
  const [ref, setRef] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const github = platform.faehigkeiten.find((c) => c.name === "github_analysieren");
  const titles = Object.fromEntries(platform.faehigkeiten.map((c) => [c.name, c.titel]));
  const submit = async () => {
    const value = repo.trim();
    if (!value) return;
    setNote(null);
    try {
      if (/^[\w.-]+\/[\w.-]+$/.test(value) || value.startsWith("https://github.com/")) await onStart("github_analysieren", { repo: value, ...(ref.trim() ? { ref: ref.trim() } : {}) });
      else await onStart("github_suchen", { begriff: value, anzahl: 5 });
      setRepo("");
      setRef("");
    } catch (reason) {
      setNote(reason instanceof Error ? reason.message : "Start nicht möglich.");
    }
  };
  return (
    <div className="cc-sp-body">
      <form className="cc-sp-start" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        <label className="grow"><span>GitHub: Suchbegriff oder <code>owner/repo</code></span>
          <input value={repo} onChange={(e) => setRepo(e.target.value)} placeholder="z. B. anthropics/skills oder „markdown editor“"
            disabled={!canEdit || !github?.aktiviert} maxLength={140} />
        </label>
        <label><span>Branch, Tag oder Commit (optional)</span>
          <input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="Hauptbranch" disabled={!canEdit || !github?.aktiviert} maxLength={100} />
        </label>
        <button type="submit" className="cc-btn accent small" disabled={!canEdit || !repo.trim() || !github?.aktiviert}>
          {/\//.test(repo) ? "Analysieren" : "Suchen"}
        </button>
      </form>
      <p className="cc-hint"><span className="cc-tag" title="Öffentliche Repos ohne Anmeldung. Kein Code wird ausgeführt, nichts installiert; eigener Arbeitsbereich je Commit.">nur lesen</span>
        {github?.sperrgrund ? ` Gesperrt: ${github.sperrgrund}` : ""}</p>
      {note && <p className="cc-warn-text" role="alert">{note}</p>}
      <ol className="cc-runs" aria-label="Aufträge">
        {platform.laeufe.length === 0 && <li className="cc-empty">Noch kein Auftrag.</li>}
        {platform.laeufe.map((run) => {
          const st = STATUS[run.status] ?? { label: run.status, tone: "off" };
          const orderId = typeof run.ergebnis?.auftrag_id === "string" ? run.ergebnis.auftrag_id : null;
          return (
            <li key={run.id} className={`cc-job ${st.tone}`}>
              <div className="cc-job-head">
                <span className={`cc-run-state ${st.tone}`}>{st.label}</span>
                <b>{titles[run.faehigkeit] ?? run.faehigkeit}</b>
                <span className="cc-job-input">{run.eingabe.repo ?? run.eingabe.titel ?? run.eingabe.skill ?? run.eingabe.begriff ?? Object.values(run.eingabe)[0] ?? ""}</span>
                <span className="cc-hint">{clock(run.start)} · {SOURCE[run.quelle] ?? run.quelle}{duration(run) ? ` · ${duration(run)}` : ""}</span>
                <span className="cc-run-actions">
                  {orderId && <button type="button" className="cc-btn small" onClick={() => onOpenOrder(orderId)}>{orderId} öffnen</button>}
                  {run.status === "WAITING_FOR_APPROVAL" && <button type="button" className="cc-btn small" onClick={onOpenApprovals}>Zu den Freigaben</button>}
                  {run.abbrechbar && canEdit && <button type="button" className="cc-btn small" disabled={busy === run.id} onClick={() => onAction(run.id, "abbrechen")}>Abbrechen</button>}
                  {run.wiederholbar && canEdit && <button type="button" className="cc-btn small" disabled={busy === run.id} onClick={() => onAction(run.id, "wiederholen")}>Wiederaufnehmen</button>}
                </span>
              </div>
              {(run.status === "RUNNING" || run.status === "PLANNED") && run.fortschritt && <p className="cc-job-progress">{run.fortschritt}</p>}
              {run.schritte && run.schritte.length > 1 && (
                <ol className="cc-steps" aria-label="Schritte">{run.schritte.map((s, i) => <li key={i} className={(STATUS[s.status] ?? { tone: "off" }).tone}>{s.werkzeug}</li>)}</ol>
              )}
              {run.fehler && <p className="cc-warn-text">{run.fehler}</p>}
              {run.ergebnis?.github && <RepoResult summary={run.ergebnis.github} />}
              {run.ergebnis?.treffer && (
                <ul className="cc-hits">{run.ergebnis.treffer.map((h) => (
                  <li key={h.repo}>
                    <span><b>{h.repo}</b> <span className="cc-hint">★ {h.sterne} · {h.sprache || "–"} · {h.lizenz || "keine Lizenzangabe"}</span><br />{h.beschreibung}</span>
                    {canEdit && <button type="button" className="cc-btn small" onClick={() => void onStart("github_analysieren", { repo: h.repo })}>Analysieren</button>}
                  </li>
                ))}</ul>
              )}
              {!run.ergebnis?.github && !run.ergebnis?.treffer && run.ergebnis?.text && run.status !== "RUNNING" && !orderId && (
                <p className="cc-job-text">{String(run.ergebnis.text).slice(0, 600)}</p>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

function RepoResult({ summary }: { summary: GitHubSummary }) {
  const lic = summary.lizenz.erkannt || summary.lizenz.laut_github;
  return (
    <div className="cc-repo">
      <dl>
        <dt>Herkunft</dt><dd><a href={summary.url} target="_blank" rel="noopener noreferrer">{summary.repo}</a>{summary.privat ? " · privat (freigegeben)" : " · öffentlich"}</dd>
        <dt>Commit</dt><dd className="mono"><a href={`${summary.url}/tree/${summary.commit}`} target="_blank" rel="noopener noreferrer">{summary.commit.slice(0, 12)}</a> · {summary.ref}</dd>
        <dt>Lizenz</dt><dd>{lic || "keine erkannt"}{summary.lizenz.datei ? ` (${summary.lizenz.datei})` : " – keine Lizenzdatei"}</dd>
        <dt>Stack</dt><dd>{[...summary.stack.manifeste, ...summary.stack.sprachen].join(" · ") || "–"}</dd>
        <dt>Struktur</dt><dd>{summary.struktur.dateien} Dateien · {summary.struktur.oberste_ebene.slice(0, 10).join(", ")}</dd>
        {summary.stack.abhaengigkeiten.length > 0 && <><dt>Abhängigkeiten</dt><dd>{summary.stack.abhaengigkeiten.slice(0, 14).join(", ")}</dd></>}
        {summary.skill_dateien.length > 0 && <><dt>SKILL.md</dt><dd>{summary.skill_dateien.length} gefunden – nicht installiert, nicht vertrauenswürdig</dd></>}
      </dl>
      {summary.readme_auszug && (
        <blockquote className="cc-untrusted"><span>README ({summary.readme_datei}) – fremder Inhalt{summary.readme_verdacht ? `, ${summary.readme_verdacht} Verdachtsstellen` : ""}</span>{summary.readme_auszug}</blockquote>
      )}
      <p className="cc-hint">{summary.hinweis} Arbeitsbereich: <code>{summary.arbeitsbereich.replace(/^.*\/\.jarvis\//, "~/.jarvis/")}</code></p>
    </div>
  );
}

function Library({ platform, canEdit, onToggleSkill }: PlatformProps & { platform: Platform }) {
  return (
    <div className="cc-sp-body">
      <h4>Ausführbare Fähigkeiten <span className="cc-hint">Jarvis-Code, als Werkzeug in Studio und Echtzeit</span></h4>
      <ul className="cc-lib">
        {platform.faehigkeiten.map((c) => (
          <li key={c.name} className={c.aktiviert ? "" : "off"}>
            <div className="cc-lib-main">
              <b>{c.titel}</b> <code>{c.name}</code>
              <span className={`cc-perm ${c.berechtigung}`}>{PERM_LABEL[c.berechtigung] ?? c.berechtigung}</span>
              <span className="cc-hint">{c.herkunft} · v{c.version} · Zeitlimit {c.zeitlimit_s} s{c.abbrechbar ? " · abbrechbar" : ""}</span>
              {!c.aktiviert && <p className="cc-warn-text">{c.sperrgrund}</p>}
            </div>
            <span className={`cc-lib-state ${c.aktiviert ? "on" : ""}`}>{c.aktiviert ? "verfügbar" : "gesperrt"}</span>
          </li>
        ))}
      </ul>
      <h4>Jarvis-Skills <span className="cc-hint">Markdown-Anleitungen; ausgeführt wird nur über registrierte Werkzeuge</span></h4>
      <ul className="cc-lib">
        {platform.skills.map((s) => (
          <li key={s.id} className={s.aktiviert ? "" : "off"}>
            <div className="cc-lib-main">
              <b>{s.name}</b> <span className="cc-flag">{s.herkunft}</span>
              <span className="cc-hint">{s.quelle} · v{s.version} · <span className="mono">{s.commit.slice(0, 19)}</span></span>
              <p>{s.beschreibung.slice(0, 180)}</p>
              <p className="cc-hint">Rahmen: {RAHMEN[s.rahmen]}
                {s.werkzeuge.length > 0 && ` · Werkzeuge: ${s.werkzeuge.join(", ")}`}
                {s.fehlende_werkzeuge.length > 0 && ` · fehlen: ${s.fehlende_werkzeuge.join(", ")}`}
                {s.berechtigungen.extern?.length ? ` · außenwirksam nur mit Freigabe: ${s.berechtigungen.extern.join(", ")}` : ""}</p>
              {s.verdachtsstellen > 0 && <p className="cc-warn-text">{s.verdachtsstellen} Verdachtsstellen beim Import – erst prüfen.</p>}
              {!s.aktiviert && s.sperrgrund && <p className="cc-hint">{s.sperrgrund}</p>}
            </div>
            <button type="button" className={`cc-btn small ${s.aktiviert ? "" : "accent"}`} disabled={!canEdit}
              aria-pressed={s.aktiviert} onClick={() => onToggleSkill(s.id, !s.aktiviert)}>{s.aktiviert ? "Deaktivieren" : "Aktivieren"}</button>
          </li>
        ))}
      </ul>
      <h4>Cloud-Skills <span className="cc-hint">{platform.cloud.anbieter}</span></h4>
      <p className="cc-lib-note">
        Anthropic Agent Skills ({platform.cloud.verfuegbar.join(", ")}) laufen in Anthropics Container – ohne Internet und ohne Zugriff auf Jarvis-Werkzeuge.
        {platform.cloud.erlaubt.length ? ` Freigeschaltet: ${platform.cloud.erlaubt.join(", ")}.` : ""} {platform.cloud.sperrgrund}
      </p>
      <h4>GitHub und Code-Rechte</h4>
      <p className="cc-lib-note">
        Öffentliche Repositories: lesen ohne Anmeldung. Private: {platform.github.privat_moeglich
          ? `freigegeben für ${platform.github.freigegeben.join(", ")}`
          : `nicht eingerichtet (${platform.github.token ? "Token vorhanden" : "kein JARVIS_GITHUB_TOKEN"}, ${platform.github.freigegeben.length ? `${platform.github.freigegeben.length} freigegeben` : "keine Repos in github_freigegeben"})`}.
        {" "}Code ausführen, Repo schreiben, Push, Merge und Veröffentlichen sind getrennte Rechte – keine Fähigkeit nutzt sie; jede bräuchte deine Freigabe und arbeitet nie direkt auf <code>main</code>.
      </p>
    </div>
  );
}
