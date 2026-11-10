import type { CalendarData, CalendarEvent } from "./api";
import { phaseOf, timeRange } from "./model";

const PHASE: Record<string, string> = { vorbereitung: "In den nächsten 24 Stunden", laeuft: "Läuft gerade", followup: "Follow-up fällig" };
const ANSWER: Record<string, string> = { accepted: "zugesagt", declined: "abgesagt", tentative: "vielleicht", needsAction: "offen" };

/** Eigene Termin-Detailansicht: alle echten Felder aus dem Google-Kalender, Aktionen nur über Jarvis (mit Freigabe). */
export function EventDetail({ event, data, canAsk, onAsk, onBack }: {
  event: CalendarEvent; data: CalendarData | null; canAsk: boolean; onAsk: (text: string) => void; onBack: () => void;
}) {
  const tz = data?.zeitzone || event.zeitzone || "Europe/Berlin";
  const start = event.beginn ? new Date(event.beginn) : event.datum_beginn ? new Date(`${event.datum_beginn}T12:00:00Z`) : null;
  const phase = phaseOf(event, new Date());
  const conflicts = (data?.konflikte ?? []).filter((k) => k.a === event.id || k.b === event.id);
  const dateLong = start ? start.toLocaleDateString("de-DE", { timeZone: tz, weekday: "long", day: "numeric", month: "long", year: "numeric" }) : "Datum unbekannt";
  return (
    <div className="cc-event-page">
      <button type="button" className="cc-back-link" onClick={onBack}>← Zurück zum Kalender</button>
      <div className="cc-card-box">
        <div className="cc-event-hero">
          {start && (
            <div className="cc-date-chip" aria-hidden="true">
              <span>{start.toLocaleDateString("de-DE", { timeZone: tz, month: "short" })}</span>
              <strong>{start.toLocaleDateString("de-DE", { timeZone: tz, day: "numeric" })}</strong>
            </div>
          )}
          <div style={{ flex: 1, minWidth: 220 }}>
            <h2>{event.titel || "(ohne Titel)"}</h2>
            <p style={{ color: "var(--cc-muted)", margin: "4px 0 0" }}>{dateLong} · {timeRange(event, tz)}</p>
            <div style={{ display: "flex", gap: 6, marginTop: 10, flexWrap: "wrap" }}>
              {phase && <span className="cc-state RUNNING">{PHASE[phase]}</span>}
              {conflicts.length > 0 && <span className="cc-state fehler">Überschneidung</span>}
              {event.status === "tentative" && <span className="cc-tag">vorläufig</span>}
              {!event.gueltig && <span className="cc-state fehler">Zeitangabe ungültig</span>}
            </div>
          </div>
        </div>
      </div>
      <div className="cc-grid two">
        <section className="cc-card-box">
          <h3>Details</h3>
          <dl className="cc-facts">
            <dt>Zeit</dt><dd>{timeRange(event, tz)}{event.ganztaegig ? "" : ` (${event.zeitzone})`}</dd>
            <dt>Kalender</dt><dd>{event.kalender || "–"}</dd>
            <dt>Ort</dt><dd>{event.ort || "–"}</dd>
            <dt>Online-Meeting</dt><dd>{event.meeting_link ? <a href={event.meeting_link} target="_blank" rel="noopener noreferrer">Meeting öffnen ↗</a> : "–"}</dd>
            <dt>Belegt</dt><dd>{event.frei ? "als frei markiert" : "ja"}</dd>
            {event.serie_id && (<><dt>Serie</dt><dd>{event.ausnahme ? "Ausnahme einer Serie" : "Teil einer Serie"}</dd></>)}
            {event.link && (<><dt>Google Kalender</dt><dd><a href={event.link} target="_blank" rel="noopener noreferrer">Im Kalender öffnen ↗</a></dd></>)}
          </dl>
          {conflicts.length > 0 && (
            <p className="cc-hint">Überschneidet sich mit: {conflicts.map((k) => (k.a === event.id ? k.titel[1] : k.titel[0])).join(", ")}</p>
          )}
        </section>
        <section className="cc-card-box">
          <h3>Teilnehmer ({event.teilnehmer.length})</h3>
          {event.teilnehmer.length === 0 ? <p className="cc-empty">Keine Teilnehmer eingetragen.</p> : (
            <ul className="cc-list">
              {event.teilnehmer.map((t) => (
                <li key={t.email || t.name} className="cc-row">
                  <span className="grow"><span className="title">{t.name || t.email}{t.ich ? " (du)" : ""}</span>
                    {t.name && <span className="meta">{t.email}</span>}</span>
                  <span className="cc-tag">{ANSWER[t.antwort] ?? t.antwort ?? "–"}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      </div>
      {canAsk && (
        <section className="cc-card-box">
          <h3>Mit Jarvis</h3>
          <p className="cc-hint" style={{ marginTop: 0, marginBottom: 10 }}>Jarvis bereitet vor; alles, was nach außen geht (Mail, Terminänderung), kommt erst als Vorlage zur Freigabe.</p>
          <div className="cc-actions">
            <button type="button" className="cc-btn accent" onClick={() => onAsk(`Bereite mich auf den Termin "${event.titel}" (${dateLong}, ${timeRange(event, tz)}) vor: Teilnehmer, Kontext aus Mails und CRM, Ziel und drei Fragen.`)}>
              Vorbereitung erstellen</button>
            <button type="button" className="cc-btn" onClick={() => onAsk(`Entwirf eine kurze Follow-up-Mail zum Termin "${event.titel}" (${dateLong}) als Vorlage.`)}>
              Follow-up entwerfen</button>
          </div>
        </section>
      )}
    </div>
  );
}
