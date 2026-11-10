import { useState } from "react";
import type { ContentItem, ContentOverview } from "./api";

const PLATFORM: Record<string, string> = { tiktok: "TikTok", instagram: "Instagram", youtube_shorts: "YouTube Shorts", youtube: "YouTube" };

function Card({ item, columns, canEdit, onMove }: { item: ContentItem; columns: { id: string; titel: string }[]; canEdit: boolean; onMove: (id: string, status: string) => void }) {
  const [open, setOpen] = useState(false);
  const index = columns.findIndex((c) => c.id === item.status);
  const best = [...item.hooks].sort((a, b) => (b.summe ?? -1) - (a.summe ?? -1))[0];
  return (
    <article className="cc-card" aria-label={`Content ${item.titel}`}>
      <button type="button" className="cc-card-main" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="cc-card-title">{item.titel}</span>
        <span className="cc-card-meta">{item.plattformen.map((p) => PLATFORM[p] ?? p).join(" · ") || "keine Plattform"}{item.hooks.length ? ` · ${item.hooks.length} Hooks` : ""}</span>
      </button>
      {open && (
        <div className="cc-content-detail">
          {item.thema && <p><strong>Thema:</strong> {item.thema}</p>}
          {item.ziel && <p><strong>Ziel:</strong> {item.ziel}</p>}
          {best && <p><strong>Bester Hook{best.summe != null ? ` (${best.summe}/20)` : ""}:</strong> {best.text}{best.belegt_durch ? ` – Beleg: ${best.belegt_durch}` : ""}</p>}
          {item.quellen.length > 0 && <p><strong>Quellen:</strong> {item.quellen.join("; ")}</p>}
          {item.ki_hinweis && <p><strong>KI-Hinweis:</strong> {item.ki_hinweis}</p>}
          {item.ergebnisse.length > 0 && (
            <ul>{item.ergebnisse.map((e, i) => <li key={i}>{PLATFORM[e.plattform] ?? e.plattform} · {e.erfasst.slice(0, 10)} · {Object.entries(e).filter(([k]) => !["plattform", "erfasst", "quelle"].includes(k)).map(([k, v]) => `${k}: ${v}`).join(", ")} ({e.quelle})</li>)}</ul>
          )}
        </div>
      )}
      {canEdit && (
        <div className="cc-card-move">
          <button type="button" disabled={index <= 0} onClick={() => onMove(item.id, columns[index - 1].id)} aria-label={`${item.titel} zurück nach ${columns[index - 1]?.titel ?? ""}`}>‹</button>
          <button type="button" disabled={index >= columns.length - 1} onClick={() => onMove(item.id, columns[index + 1].id)} aria-label={`${item.titel} weiter nach ${columns[index + 1]?.titel ?? ""}`}>›</button>
        </div>
      )}
    </article>
  );
}

/** Content-Workflow von der Idee bis zur Auswertung. Es wird nichts veröffentlicht; ab „Freigegeben“ setzt nur der Nutzer. */
export function ContentPanel({ data, error, canEdit, onMove, onAsk }: { data: ContentOverview | null; error: string | null; canEdit: boolean; onMove: (id: string, status: string) => void; onAsk: (t: string) => void }) {
  if (error) return <p className="cc-warn-text">{error}</p>;
  if (!data) return <p className="cc-empty">Wird geladen …</p>;
  return (
    <div className="cc-content">
      <p className="cc-source">{data.quelle}. Jarvis bringt Einträge höchstens bis „Review“; Freigabe und Veröffentlichung entscheidest du selbst.</p>
      {data.eintraege.length === 0 && (
        <p className="cc-empty">Noch kein Content-Eintrag. {canEdit && <button type="button" className="cc-link" onClick={() => onAsk("Lass uns eine Content-Idee entwickeln. Frag mich nach Thema, Ziel und Zielgruppe und liefere mehrere klar unterschiedliche Hooks mit Quellen, wo Fakten vorkommen.")}>Idee mit Jarvis entwickeln</button>}</p>
      )}
      <div className="cc-content-board">
        {data.spalten.map((col) => {
          const items = data.eintraege.filter((i) => i.status === col.id);
          return (
            <section key={col.id} className="cc-pipe-col" aria-label={col.titel}>
              <h4>{col.titel} <span className="mono">{items.length}</span></h4>
              <div className="cc-content-cards">{items.map((i) => <Card key={i.id} item={i} columns={data.spalten} canEdit={canEdit} onMove={onMove} />)}</div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
