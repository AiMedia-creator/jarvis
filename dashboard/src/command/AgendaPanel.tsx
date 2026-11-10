import type { CalendarData, CalendarEvent } from "./api";
import { agendaDays, timeRange } from "./model";

const PHASE: Record<string, string> = { vorbereitung: "Vorbereitung", laeuft: "Läuft gerade", followup: "Follow-up" };

export function AgendaPanel({
  data,
  query,
  limitDays,
  canAsk,
  onAsk,
  onSelect,
}: {
  data: CalendarData | null;
  query: string;
  limitDays?: number;
  canAsk: boolean;
  onAsk: (text: string) => void;
  onSelect: (event: CalendarEvent) => void;
}) {
  if (!data) return <p className="cc-empty">Lade Termine …</p>;
  if (data.status !== "ok") return <p className="cc-empty">{data.hinweis || "Kalender nicht verfügbar."}</p>;
  const tz = data.zeitzone || "Europe/Berlin";
  const q = query.trim().toLowerCase();
  const events = data.termine.filter((e) => !q || `${e.titel} ${e.ort} ${e.kalender}`.toLowerCase().includes(q));
  const conflictIds = new Set(data.konflikte.flatMap((k) => [k.a, k.b]));
  const days = agendaDays(events, tz).slice(0, limitDays ?? 14);
  if (!days.length) return <p className="cc-empty">Keine Termine im Zeitraum.</p>;
  return (
    <div className="cc-agenda">
      {days.map((day) => (
        <section key={day.key} aria-label={day.label}>
          <h3>{day.label}</h3>
          <ul>
            {day.items.map((e) => (
              <li key={`${day.key}-${e.id}`} className={`cc-event ${conflictIds.has(e.id) ? "conflict" : ""} ${e.phase ?? ""}`}>
                <button type="button" className="cc-event-main" onClick={() => onSelect(e)}>
                  <span className="cc-event-time">{timeRange(e, tz)}</span>
                  <span className="cc-event-title">{e.titel}</span>
                  <span className="cc-event-meta">
                    {e.kalender}
                    {e.ort ? ` · ${e.ort}` : ""}
                    {e.serie_id ? " · Serie" : ""}
                    {conflictIds.has(e.id) ? " · Konflikt" : ""}
                    {!e.gueltig ? " · ungültige Zeit" : ""}
                  </span>
                </button>
                {e.phase && <span className="cc-tag">{PHASE[e.phase]}</span>}
                {canAsk && e.phase === "vorbereitung" && (
                  <button type="button" className="cc-btn small"
                    onClick={() => onAsk(`Bereite mich auf den Termin "${e.titel}" (${timeRange(e, tz)}, ${day.label}) vor.`)}>
                    Vorbereiten lassen
                  </button>
                )}
                {canAsk && e.phase === "followup" && (
                  <button type="button" className="cc-btn small"
                    onClick={() => onAsk(`Entwirf das Follow-up zum Termin "${e.titel}" (${day.label}) als Vorlage.`)}>
                    Follow-up entwerfen
                  </button>
                )}
              </li>
            ))}
          </ul>
        </section>
      ))}
      <p className="cc-hint">Zeitzone {tz}. „Vorbereiten“ und „Follow-up“ fragen Jarvis erst, wenn du klickst.</p>
    </div>
  );
}
