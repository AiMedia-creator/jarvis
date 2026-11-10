import type { Capability, CapabilityRun } from "./api";

const STATUS: Record<CapabilityRun["status"], { label: string; tone: string }> = {
  RUNNING: { label: "läuft", tone: "run" }, PLANNED: { label: "geplant", tone: "run" },
  COMPLETED: { label: "erledigt", tone: "ok" }, FAILED: { label: "fehlgeschlagen", tone: "err" },
  CANCELLED: { label: "abgebrochen", tone: "off" }, WAITING_FOR_APPROVAL: { label: "wartet auf Freigabe", tone: "wait" },
};
const SOURCE: Record<string, string> = { web: "Studio", stimme: "Sprache", echtzeit: "Echtzeit", hintergrund: "Hintergrund", jarvis: "Jarvis" };
const clock = (s: number) => new Date(s * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** Fähigkeiten, die Jarvis zur Laufzeit wirklich ausführen kann, mit ihren letzten Läufen. */
export function CapabilityPanel({ capabilities, runs, error, canEdit, busy, onAction, onOpenOrder }: {
  capabilities: Capability[] | null; runs: CapabilityRun[]; error: string | null; canEdit: boolean; busy: string | null;
  onAction: (id: string, action: "abbrechen" | "wiederholen") => void; onOpenOrder: (id: string) => void;
}) {
  if (error) return <section className="cc-caps"><h3>Fähigkeiten</h3><p className="cc-warn-text">{error}</p></section>;
  if (!capabilities) return <section className="cc-caps"><h3>Fähigkeiten</h3><p className="cc-empty">Wird geladen …</p></section>;
  return (
    <section className="cc-caps" aria-label="Fähigkeiten von Jarvis">
      <header className="cc-caps-head">
        <h3>Fähigkeiten</h3>
        <span className="cc-hint">Was Jarvis selbst ausführen kann – als Job, unabhängig vom Gespräch.</span>
      </header>
      {capabilities.map((cap) => {
        const own = runs.filter((r) => r.faehigkeit === cap.name);
        const running = own.filter((r) => r.status === "RUNNING" || r.status === "PLANNED");
        return (
          <article key={cap.name} className={`cc-cap ${running.length ? "is-running" : ""}`}>
            <div className="cc-cap-def">
              <div className="cc-cap-title">
                <strong>{cap.titel}</strong>
                <code>{cap.name}</code>
                <span className={`cc-perm ${cap.berechtigung}`} title={cap.berechtigung_text}>
                  {cap.berechtigung === "extern" ? "extern · nur mit Freigabe" : cap.berechtigung === "lokal" ? "lokal · ohne Freigabe" : "nur lesen"}
                </span>
              </div>
              <p>{cap.berechtigung_text}. Eingaben: {Object.keys(cap.eingabe.properties)
                .map((k) => (cap.eingabe.required.includes(k) ? `${k}*` : k)).join(", ")}.</p>
              <p className="cc-hint">Zum Beispiel sagen: „Leg einen Auftrag an: …“ – Jarvis trägt nur ein, was du nennst.</p>
            </div>
            <ol className="cc-runs" aria-label={`Letzte Läufe: ${cap.titel}`}>
              {own.length === 0 && <li className="cc-empty">Noch kein Lauf in dieser Jarvis-Sitzung.</li>}
              {own.slice(0, 5).map((run) => {
                const st = STATUS[run.status] ?? { label: run.status, tone: "off" };
                const orderId = typeof run.ergebnis?.auftrag_id === "string" ? run.ergebnis.auftrag_id : null;
                return (
                  <li key={run.id} className={`cc-run-row ${st.tone}`}>
                    <span className={`cc-run-state ${st.tone}`}>{st.label}</span>
                    <span className="cc-run-main">
                      <b>{run.eingabe.titel ?? Object.values(run.eingabe)[0] ?? "–"}</b>
                      <span className="cc-hint">
                        {clock(run.start)} · {SOURCE[run.quelle] ?? run.quelle}
                        {run.ende ? ` · ${Math.max(0, Math.round((run.ende - run.start) * 1000))} ms` : ""}
                        {run.eingabe.kunde ? ` · Kunde ${run.eingabe.kunde}` : ""}
                      </span>
                      {run.fehler && <span className="cc-warn-text">{run.fehler}</span>}
                    </span>
                    <span className="cc-run-actions">
                      {orderId && <button type="button" className="cc-btn small" onClick={() => onOpenOrder(orderId)}>{orderId} öffnen</button>}
                      {run.abbrechbar && canEdit && (
                        <button type="button" className="cc-btn small" disabled={busy === run.id} onClick={() => onAction(run.id, "abbrechen")}>Abbrechen</button>
                      )}
                      {(run.status === "FAILED" || run.status === "CANCELLED") && canEdit && (
                        <button type="button" className="cc-btn small" disabled={busy === run.id} onClick={() => onAction(run.id, "wiederholen")}>Wiederholen</button>
                      )}
                    </span>
                  </li>
                );
              })}
            </ol>
          </article>
        );
      })}
    </section>
  );
}
