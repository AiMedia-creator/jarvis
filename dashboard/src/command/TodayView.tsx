import { useEffect, useRef, useState } from "react";
import type { AgentCenterData, CalendarData, CalendarEvent } from "./api";
import { AgendaPanel } from "./AgendaPanel";
import { duration, isActive, jobState } from "./AgentCenter";
import { Icon } from "./icons";
import type { QueueItem, Suggestion } from "./today";

const KIND_ICON: Record<QueueItem["kind"], string> = {
  freigabe: "approvals", job: "agents", auftrag: "orders", followup: "phone", crm: "phone", content: "news",
  kalender: "calendar", ablauf: "automation", verbindung: "link",
};
const VISIBLE = 6;
const clock = (ts: number) => new Date(ts * 1000).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit" });
const greeting = () => {
  const h = new Date().getHours();
  return h < 11 ? "Guten Morgen" : h < 18 ? "Guten Tag" : "Guten Abend";
};

function useSeconds(active: boolean): number {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(t);
  }, [active]);
  return now;
}

export type TodayProps = {
  dateLabel: string;
  queue: QueueItem[];
  /** false, solange Aufträge und Freigaben noch nie geantwortet haben. */
  ready: boolean;
  /** Quellen, die nicht gelesen werden konnten – die Liste ist dann ausdrücklich unvollständig. */
  unchecked: string[];
  suggestions: Suggestion[];
  agents: AgentCenterData | null;
  agentError: string | null;
  calendar: CalendarData | null;
  nextEvent: { titel: string; zeit: string } | null;
  canAct: boolean;
  connected: boolean;
  onAsk: (text: string) => void;
  onOpen: (item: QueueItem) => void;
  onOpenJob: (id?: string) => void;
  onOpenEvent: (event: CalendarEvent) => void;
  onTalk: () => void;
};

/** Startseite: Lage verstehen (was braucht mich, was läuft, was wurde fertig) und sofort den nächsten Auftrag übergeben. */
export function TodayView({
  dateLabel, queue, ready, unchecked, suggestions, agents, agentError, calendar, nextEvent, canAct, connected,
  onAsk, onOpen, onOpenJob, onOpenEvent, onTalk,
}: TodayProps) {
  const [text, setText] = useState("");
  const [all, setAll] = useState(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const jobs = agents?.jobs ?? [];
  const active = jobs.filter(isActive);
  const done = jobs.filter((j) => j.status === "COMPLETED").slice(0, 3);
  const now = useSeconds(active.length > 0);
  const shown = all ? queue : queue.slice(0, VISIBLE);

  const submit = () => {
    const value = text.trim();
    if (!value || !canAct) return;
    onAsk(value);
    setText("");
  };
  const headline = !ready ? "Lage wird geladen …"
    : queue.length === 0 ? "Nichts wartet auf dich."
    : queue.length === 1 ? "Eine Sache braucht dich." : `${queue.length} Dinge brauchen dich.`;
  const facts = [
    active.length ? `${active.length} ${active.length === 1 ? "Agent arbeitet" : "Agenten arbeiten"}` : agents ? "Kein Agent arbeitet gerade" : null,
    nextEvent ? `Nächster Termin: ${nextEvent.titel}, ${nextEvent.zeit}` : null,
  ].filter(Boolean);

  return (
    <div className="td">
      <header className="td-head">
        <p className="td-date">{dateLabel}</p>
        <h2>{greeting()}. <span>{headline}</span></h2>
        {facts.length > 0 && <p className="td-facts">{facts.join(" · ")}</p>}
      </header>

      <form className="td-command" aria-label="Auftrag an Jarvis" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <label htmlFor="td-command-input">Was soll Jarvis erledigen?</label>
        <div className="td-command-row">
          <textarea id="td-command-input" ref={input} rows={1} value={text} maxLength={8000} disabled={!canAct}
            placeholder={canAct ? "z. B. „Bereite das Angebot für Weber vor und leg einen Auftrag an“" : "Nur Ansicht – ein anderes Fenster steuert Jarvis"}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } }} />
          <button type="submit" className="cc-btn accent" disabled={!canAct || !text.trim()}>Übergeben</button>
          <button type="button" className="cc-btn td-talk" onClick={onTalk}><Icon name="mic" /> Sprechen</button>
        </div>
        {!connected && <p className="td-command-note" role="status">Keine Verbindung zu Jarvis – Aufträge kommen erst nach der Neuverbindung an.</p>}
        {canAct && suggestions.length > 0 && (
          <ul className="td-suggest" aria-label="Vorschläge – füllen das Feld, senden nichts">
            {suggestions.map((s) => (
              <li key={s.label}><button type="button" onClick={() => { setText(s.text); input.current?.focus(); }}>{s.label}</button></li>
            ))}
          </ul>
        )}
      </form>

      <div className="td-cols">
        <section className="td-queue" aria-label="Braucht dich">
          <header><h3>Braucht dich</h3>{queue.length > 0 && <span className="td-count">{queue.length}</span>}</header>
          {!ready ? (
            <ul className="td-skeleton" aria-hidden="true"><li /><li /><li /></ul>
          ) : queue.length === 0 ? (
            <div className="td-clear">
              <Icon name="approvals" />
              <p><strong>Alles entschieden.</strong> Keine Freigabe wartet, nichts ist überfällig, kein Job ist fehlgeschlagen.</p>
            </div>
          ) : (
            <ol className="td-items">
              {shown.map((item) => (
                <li key={item.id} className={`td-item ${item.tone}`}>
                  <span className="td-item-icon" aria-hidden="true"><Icon name={KIND_ICON[item.kind]} /></span>
                  <div className="td-item-main">
                    <span className="td-item-label">{item.label}</span>
                    <strong>{item.title}</strong>
                    <p>{item.reason}</p>
                  </div>
                  <button type="button" className="cc-btn small" onClick={() => onOpen(item)}
                    aria-label={`${item.action}: ${item.title}`}>{item.action}</button>
                </li>
              ))}
            </ol>
          )}
          {queue.length > VISIBLE && (
            <button type="button" className="cc-link td-more" aria-expanded={all} onClick={() => setAll((v) => !v)}>
              {all ? "Weniger zeigen" : `${queue.length - VISIBLE} weitere zeigen`}
            </button>
          )}
          {unchecked.length > 0 && (
            <p className="td-unchecked" role="status">Nicht geprüft, weil nicht erreichbar: {unchecked.join(", ")}. Diese Liste kann unvollständig sein.</p>
          )}
        </section>

        <div className="td-side">
          <section aria-label="Läuft gerade">
            <header><h3>Läuft gerade</h3><button type="button" className="cc-link" onClick={() => onOpenJob()}>Alle Agenten</button></header>
            {!agents ? <p className="cc-empty">{agentError ?? "Lädt …"}</p> : active.length === 0 ? (
              <p className="cc-empty">Kein Agent arbeitet. Übergib oben einen Auftrag – er erscheint hier mit Schritten und Laufzeit.</p>
            ) : (
              <ul className="td-jobs">
                {active.slice(0, 4).map((j) => (
                  <li key={j.id}>
                    <button type="button" onClick={() => onOpenJob(j.id)}>
                      <span className={`ac-dot ${j.status}`} aria-hidden="true" />
                      <span className="td-job-main">
                        <strong>{j.titel}</strong>
                        <span>{j.agent.name} · {j.status === "WAITING_FOR_APPROVAL" ? "wartet auf deine Freigabe" : `seit ${duration(now - j.start)}`}
                          {j.schritte_gesamt > 0 && ` · Schritt ${j.schritte_fertig}/${j.schritte_gesamt}`}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-label="Zuletzt fertig">
            <header><h3>Zuletzt fertig</h3></header>
            {!agents ? <p className="cc-empty">{agentError ? "Nicht erreichbar." : "Lädt …"}</p> : done.length === 0 ? (
              <p className="cc-empty">Noch kein abgeschlossener Job.</p>
            ) : (
              <ul className="td-jobs">
                {done.map((j) => (
                  <li key={j.id}>
                    <button type="button" onClick={() => onOpenJob(j.id)}>
                      <span className="ac-dot COMPLETED" aria-hidden="true" />
                      <span className="td-job-main">
                        <strong>{j.titel}</strong>
                        <span>{jobState(j).label} um {clock(j.ende ?? j.start)}{j.ergebnis ? ` · ${j.ergebnis.replace(/\s+/g, " ").slice(0, 90)}${j.ergebnis.length > 90 ? "…" : ""}` : ""}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-label="Termine heute und morgen">
            <header><h3>Termine</h3></header>
            <AgendaPanel data={calendar} query="" limitDays={2} canAsk={canAct} onAsk={onAsk} onSelect={onOpenEvent} />
          </section>
        </div>
      </div>
    </div>
  );
}
