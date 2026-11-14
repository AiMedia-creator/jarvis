import { useState } from "react";
import type { ContentItem, ContentOverview } from "./api";

const PLATFORM: Record<string, string> = { tiktok: "TikTok", instagram: "Instagram", youtube_shorts: "YouTube Shorts", youtube: "YouTube" };

type StartSkill = (skill: string, task: string) => Promise<void>;
const SCORE: [keyof ContentItem["hooks"][number], string][] = [["klarheit", "Klarheit"], ["relevanz", "Relevanz"], ["neugier", "Neugier"], ["belegbarkeit", "Belegbarkeit"]];

function Card({ item, columns, canEdit, onMove, onStartSkill, onAsk }: { item: ContentItem; columns: { id: string; titel: string }[]; canEdit: boolean; onMove: (id: string, status: string) => void; onStartSkill: StartSkill; onAsk: (t: string) => void }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<string | null>(null);
  const start = async (skill: string, task: string) => {
    setState("Startet …");
    try { await onStartSkill(skill, task); setState("Als Job gestartet – Fortschritt im Agenten-Center."); }
    catch (e) { setState(e instanceof Error ? e.message : "Start fehlgeschlagen."); }
  };
  const published = ["veroeffentlicht", "auswertung"].includes(item.status);
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
          {item.zielgruppe && <p><strong>Zielgruppe:</strong> {item.zielgruppe}</p>}
          {best && <p><strong>Bester Hook{best.summe != null ? ` (${best.summe}/20)` : ""}:</strong> {best.text}{best.belegt_durch ? ` – Beleg: ${best.belegt_durch}` : ""}</p>}
          {item.hooks.length > 1 && (
            <ol className="ct-hooks">{item.hooks.map((h, i) => (
              <li key={i}>{h.text}<span>{SCORE.map(([k, label]) => `${label} ${h[k] ?? "–"}`).join(" · ")}{h.belegt_durch ? ` · Beleg: ${h.belegt_durch}` : h.belegbarkeit != null && Number(h.belegbarkeit) <= 2 ? " · ohne Beleg" : ""}</span></li>
            ))}</ol>
          )}
          {item.skript ? <p><strong>Skript:</strong> {item.skript.length > 360 ? item.skript.slice(0, 360) + " …" : item.skript}</p> : <p className="cc-empty">Noch kein Skript.</p>}
          {published && item.ergebnisse.length === 0 && <p className="cc-empty">Keine Kennzahlen erfasst (keine Analytics-Anbindung).</p>}
          {canEdit && (
            <div className="cc-actions">
              {item.hooks.length === 0 && <button type="button" className="cc-btn small" onClick={() => void start("content-hooks", `Entwickle Hooks für den Content-Eintrag „${item.titel}“ (ID ${item.id}) und speichere sie dort.`)}>Hooks entwickeln</button>}
              {item.hooks.length > 0 && <button type="button" className="cc-btn small" onClick={() => void start("content-skript", `Schreibe Skript, Storyboard, Shot-Liste und KI-Prompts für den Content-Eintrag „${item.titel}“ (ID ${item.id}).`)}>{item.skript ? "Skript überarbeiten / Varianten" : "Skript und Storyboard"}</button>}
              {published && <button type="button" className="cc-btn small" onClick={() => onAsk(`Werte den Content-Eintrag „${item.titel}“ aus (Skill content-auswertung). Frag mich nach den Kennzahlen, die fehlen.`)}>Auswerten</button>}
              {published && <button type="button" className="cc-btn small" onClick={() => void start("content-skript", `Wiederverwertung: Leite drei neue Beiträge aus dem veröffentlichten Eintrag „${item.titel}“ (ID ${item.id}) ab und lege sie als Ideen an.`)}>Wiederverwerten</button>}
            </div>
          )}
          {state && <p className="cc-hint" role="status">{state}</p>}
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
export function ContentPanel({ data, error, canEdit, onMove, onAsk, onStartSkill }: { data: ContentOverview | null; error: string | null; canEdit: boolean; onMove: (id: string, status: string) => void; onAsk: (t: string) => void; onStartSkill: StartSkill }) {
  const [topic, setTopic] = useState("");
  const [state, setState] = useState<string | null>(null);
  if (error) return <p className="cc-warn-text">{error}</p>;
  if (!data) return <p className="cc-empty">Wird geladen …</p>;
  const startIdea = async () => {
    if (topic.trim().length < 4) { setState("Bitte ein Thema angeben."); return; }
    setState("Startet …");
    try {
      await onStartSkill("content-hooks", `Entwickle einen Content-Eintrag mit mindestens fünf klar unterschiedlichen, bewerteten Hooks zum Thema: ${topic.trim()}`);
      setTopic(""); setState("Als Job gestartet – der Eintrag erscheint in „Idee“ bzw. „Hooks“, sobald er gespeichert ist.");
    } catch (e) { setState(e instanceof Error ? e.message : "Start fehlgeschlagen."); }
  };
  return (
    <div className="cc-content">
      {canEdit && (
        <form className="cc-card-box ct-new" onSubmit={(e) => { e.preventDefault(); void startIdea(); }}>
          <label htmlFor="ct-topic">Neue Idee: Thema, Ziel und Zielgruppe in einem Satz</label>
          <div><input id="ct-topic" value={topic} maxLength={300} placeholder="z. B. Warum 80 % der Kaltanrufe am Einstieg scheitern – für Gründer, Ziel: Leads" onChange={(e) => setTopic(e.target.value)} />
            <button type="submit" className="cc-btn accent">Hooks entwickeln</button></div>
          {state && <p className="cc-hint" role="status">{state}</p>}
          <p className="cc-hint" title="Recherche mit Quelle und Datum; Hooks bewertet nach Klarheit, Relevanz, Neugier, Belegbarkeit.">Läuft als Job der Rolle „Content-Strategie und Hooks“.</p>
        </form>
      )}
      <p className="cc-source">{data.quelle}. Jarvis bringt Einträge höchstens bis „Review“; Freigabe und Veröffentlichung entscheidest du selbst.
        Es gibt keine Anbindung an TikTok, Instagram oder YouTube – „Geplant“ und „Veröffentlicht“ setzt du von Hand, Kennzahlen nennst du Jarvis.</p>
      {data.eintraege.length === 0 && (
        <p className="cc-empty">Noch kein Content-Eintrag. {canEdit && <button type="button" className="cc-link" onClick={() => onAsk("Lass uns eine Content-Idee entwickeln. Frag mich nach Thema, Ziel und Zielgruppe und liefere mehrere klar unterschiedliche Hooks mit Quellen, wo Fakten vorkommen.")}>Idee mit Jarvis entwickeln</button>}</p>
      )}
      <div className="cc-content-board">
        {data.spalten.map((col) => {
          const items = data.eintraege.filter((i) => i.status === col.id);
          return (
            <section key={col.id} className="cc-pipe-col" aria-label={col.titel}>
              <h4>{col.titel} <span className="mono">{items.length}</span></h4>
              <div className="cc-content-cards">{items.map((i) => <Card key={i.id} item={i} columns={data.spalten} canEdit={canEdit} onMove={onMove} onStartSkill={onStartSkill} onAsk={onAsk} />)}</div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
