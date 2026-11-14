import { useCallback, useEffect, useRef, useState } from "react";
import { api, type MemoryArchiveItem, type MemoryCenterData, type MemoryFactFull, type MemoryHistory } from "./api";

const when = (iso: string | null | undefined) => {
  if (!iso) return "–";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
};
const SOURCE: Record<string, string> = {
  user: "von dir eingetragen", agent: "von Jarvis im Gespräch gespeichert", explicit_user_assertion: "aus deiner Aussage übernommen",
  mcp: "über MCP (Claude Code)", echtzeit: "im Sprachgespräch gemerkt", filesystem: "aus alter Notizdatei übernommen",
};
const CATEGORY: Record<string, string> = { preference: "Präferenz", praeferenz: "Präferenz", "präferenz": "Präferenz", project: "Projekt", note: "Notiz", firma: "Unternehmen", lektion: "Lektion", ziel: "Ziel", person: "Person", entscheidung: "Entscheidung" };
const CATEGORIES = ["note", "ziel", "preference", "entscheidung", "project", "person", "firma", "lektion"];
/** Speicherstatus jeder Äußerung in Klartext. */
export const SAVE_STATE: Record<string, { label: string; cls: string; text: string }> = {
  completed: { label: "Gespeichert", cls: "COMPLETED", text: "Äußerung und Antwort sind gesichert." },
  pending: { label: "Ausstehend", cls: "RUNNING", text: "Äußerung ist gesichert, die Antwort läuft noch." },
  interrupted: { label: "Gespeichert · unterbrochen", cls: "CANCELLED", text: "Äußerung ist gesichert; die Antwort wurde unterbrochen oder kam nie an." },
  failed: { label: "Antwort fehlgeschlagen", cls: "FAILED", text: "Äußerung ist gesichert; Jarvis konnte nicht antworten." },
};
const CHANNEL: Record<string, string> = { text: "Text", web: "Text/Studio", echtzeit: "Echtzeit-Sprache", stimme: "Sprache", hintergrund: "Hintergrundjob", telegram: "Telegram", mcp: "MCP" };

function FactRow({ fact, all, canEdit, onChanged }: { fact: MemoryFactFull; all: MemoryFactFull[]; canEdit: boolean; onChanged: (note?: string) => void }) {
  const [mode, setMode] = useState<"view" | "edit" | "history" | "delete">("view");
  const [text, setText] = useState(fact.content);
  const [history, setHistory] = useState<MemoryHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async (fn: () => Promise<unknown>, note: string) => {
    setBusy(true); setError(null);
    try { await fn(); setMode("view"); onChanged(note); }
    catch (e) { setError(e instanceof Error ? e.message : "Nicht möglich."); }
    finally { setBusy(false); }
  };
  const showHistory = async () => {
    if (mode === "history") { setMode("view"); return; }
    setMode("history"); setHistory(null);
    try { setHistory(await api.memoryHistory(fact.id)); } catch (e) { setError(e instanceof Error ? e.message : "Verlauf nicht lesbar."); }
  };
  const conflicts = fact.conflicts.map((id) => all.find((f) => f.id === id)?.content).filter(Boolean) as string[];
  return (
    <li className={`mc-fact ${fact.conflicts.length ? "conflict" : ""} ${fact.gueltig ? "" : "expired"}`}>
      {mode === "edit" ? (
        <div className="mc-edit">
          <label htmlFor={`edit-${fact.id}`}>Erinnerung korrigieren</label>
          <textarea id={`edit-${fact.id}`} rows={3} value={text} maxLength={4000} onChange={(e) => setText(e.target.value)} autoFocus />
          <div className="cc-actions">
            <button type="button" className="cc-btn small accent" disabled={busy || !text.trim() || text.trim() === fact.content}
              onClick={() => void run(() => api.memoryAction("aendern", { id: fact.id, content: text.trim() }), "Korrigiert. Die frühere Fassung steht im Verlauf.")}>Speichern</button>
            <button type="button" className="cc-btn small" onClick={() => { setMode("view"); setText(fact.content); }}>Abbrechen</button>
          </div>
        </div>
      ) : <p className="mc-content">{fact.content}</p>}
      <div className="mc-meta">
        <span className="cc-tag">{CATEGORY[fact.category] ?? fact.category}</span>
        <span>Quelle: {SOURCE[fact.source] ?? fact.source}{fact.client ? ` (${fact.client})` : ""}</span>
        <span>erfasst {when(fact.created_at)}</span>
        {fact.updated_at !== fact.created_at && <span>geändert {when(fact.updated_at)}</span>}
        {fact.consent === "vorschlag" ? <span className="mc-open">Vorschlag – wird erst nach deiner Bestätigung verwendet</span>
          : fact.confirmed_at ? <span className="mc-ok">von dir bestätigt {when(fact.confirmed_at)}</span> : <span className="mc-ok">aktiv</span>}
        {fact.valid_until && <span>{fact.gueltig ? `gültig bis ${when(fact.valid_until)}` : `abgelaufen seit ${when(fact.valid_until)}`}</span>}
        {fact.versionen > 0 && <span>{fact.versionen} frühere Fassung{fact.versionen === 1 ? "" : "en"}</span>}
      </div>
      {fact.conflicts.length > 0 && (
        <p className="mc-conflict"><strong>Widerspruch:</strong> {conflicts.length ? conflicts.map((c) => `„${c}“`).join(" · ") : "zu einer anderen Erinnerung (hier nicht geladen)"}.
          Beide bleiben erhalten, bis du entscheidest: die richtige bestätigen, die falsche korrigieren oder löschen.</p>
      )}
      {error && <p className="ac-err">{error}</p>}
      {mode === "delete" && (
        <div className="mc-confirm" role="alertdialog" aria-label="Löschen bestätigen">
          <p>Endgültig löschen? Erinnerung, frühere Fassungen und Suchindex-Eintrag werden entfernt. Gespräche im Archiv bleiben.</p>
          <div className="cc-actions">
            <button type="button" className="cc-btn small mc-danger" disabled={busy} onClick={() => void run(() => api.memoryAction("loeschen", { id: fact.id }), "Gelöscht – auch Versionen und Suchindex.")}>Endgültig löschen</button>
            <button type="button" className="cc-btn small" onClick={() => setMode("view")}>Behalten</button>
          </div>
        </div>
      )}
      {mode === "history" && (
        <div className="mc-history">
          {!history ? <p className="cc-empty">Verlauf lädt …</p> : history.versionen.length === 0 ? <p className="cc-empty">Keine früheren Fassungen.</p> : (
            <ol>{history.versionen.map((v, i) => <li key={i}><span className="mc-date">bis {when(v.changed_at)} · {v.reason}</span>{v.content}</li>)}</ol>
          )}
        </div>
      )}
      {canEdit && mode !== "edit" && mode !== "delete" && (
        <div className="cc-actions">
          {fact.consent === "vorschlag" && <button type="button" className="cc-btn small accent" disabled={busy} onClick={() => void run(() => api.memoryAction("bestaetigen", { id: fact.id }), "Bestätigt – Jarvis verwendet diese Erinnerung jetzt, wenn sie zur Frage passt.")}>Bestätigen</button>}
          <button type="button" className="cc-btn small" onClick={() => setMode("edit")}>Korrigieren</button>
          <button type="button" className="cc-btn small" aria-expanded={mode === "history"} onClick={() => void showHistory()}>Verlauf</button>
          <button type="button" className="cc-btn small" onClick={() => setMode("delete")}>Löschen</button>
        </div>
      )}
    </li>
  );
}

function ArchiveRow({ item, canEdit, onChanged }: { item: MemoryArchiveItem; canEdit: boolean; onChanged: (note?: string) => void }) {
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const state = SAVE_STATE[item.status] ?? { label: item.status, cls: "nicht_eingerichtet", text: "" };
  const remove = async () => {
    try {
      const result = await api.memoryAction("archiv_loeschen", { id: item.id });
      onChanged(`Archiv-Eintrag gelöscht${result.abgeleitet ? ` – samt ${result.abgeleitet} daraus abgeleiteter Erinnerung(en)` : ""}.`);
    } catch (e) { setError(e instanceof Error ? e.message : "Nicht gelöscht."); }
  };
  return (
    <li className="mc-turn">
      <div className="mc-meta">
        <span className={`cc-state ${state.cls}`} title={state.text}>{state.label}</span>
        <span>{when(item.created_at)}</span><span>{CHANNEL[item.kanal] ?? item.kanal}</span>
        <span className="cc-mono">{item.conversation_id}</span>
      </div>
      {item.user_text && <p className="mc-you"><b>Du</b>{item.user_text}</p>}
      {item.assistant_text ? <p className="mc-jarvis"><b>Jarvis</b>{item.assistant_text}</p> : <p className="cc-empty">{state.text || "Keine Antwort gespeichert."}</p>}
      {error && <p className="ac-err">{error}</p>}
      {canEdit && (confirm ? (
        <div className="cc-actions"><span>Diesen Gesprächszug samt abgeleiteter Erinnerungen löschen?</span>
          <button type="button" className="cc-btn small mc-danger" onClick={() => void remove()}>Löschen</button>
          <button type="button" className="cc-btn small" onClick={() => setConfirm(false)}>Behalten</button></div>
      ) : <div className="cc-actions"><button type="button" className="cc-btn small" onClick={() => setConfirm(true)}>Löschen</button></div>)}
    </li>
  );
}

/** Gedächtnis-Center: dauerhaftes Wissen und Gesprächsarchiv getrennt; der Suchindex ist daraus jederzeit neu aufbaubar. */
export function MemoryCenter({ canEdit }: { canEdit: boolean }) {
  const [data, setData] = useState<MemoryCenterData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<"wissen" | "archiv">("wissen");
  const [query, setQuery] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [category, setCategory] = useState("note");
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);
  const load = useCallback(async (q: string) => {
    const mine = ++seq.current;
    try {
      const result = await api.memoryCenter(q, 80);
      if (mine !== seq.current) return;
      if (!Array.isArray(result?.wissen) || !Array.isArray(result?.archiv)) throw new Error("Unerwartete Antwort vom Gedächtnis.");
      setData(result); setError(null);
    } catch (e) {
      if (mine === seq.current) setError(e instanceof Error && /404/.test(e.message) ? "Der Jarvis-Server ist älter als dieses Dashboard – Jarvis neu starten." : e instanceof Error ? e.message : "Gedächtnis nicht erreichbar.");
    }
  }, []);
  useEffect(() => {
    const t = window.setTimeout(() => void load(query.trim()), query ? 250 : 0);
    return () => window.clearTimeout(t);
  }, [query, load]);
  const changed = (text?: string) => { if (text) setNote(text); void load(query.trim()); };
  const add = async () => {
    setBusy(true); setNote(null);
    try {
      const result = await api.memoryAction("speichern", { content: draft.trim(), category });
      const entry = result.eintrag ?? {};
      setNote(entry.duplicate ? "Das stand schon im Gedächtnis – nichts doppelt gespeichert."
        : entry.conflicts?.length ? "Gespeichert. Achtung: widerspricht einer älteren Erinnerung – beide bleiben erhalten, bitte entscheiden." : "Gespeichert.");
      setDraft(""); void load(query.trim());
    } catch (e) { setNote(e instanceof Error ? e.message : "Nicht gespeichert."); }
    finally { setBusy(false); }
  };
  const exportAll = async () => {
    setBusy(true);
    try {
      const result = await api.memoryExport();
      if (result.inhalt) {
        const url = URL.createObjectURL(new Blob([JSON.stringify(result.inhalt, null, 1)], { type: "application/json" }));
        const a = document.createElement("a");
        a.href = url; a.download = result.datei.split("/").pop() ?? "gedaechtnis.json"; a.click();
        window.setTimeout(() => URL.revokeObjectURL(url), 2000);
      }
      setNote(`Export geschrieben: ${result.datei} (${result.wissen} Erinnerungen, ${result.versionen} frühere Fassungen, ${result.archiv} Gesprächszüge).`);
    } catch (e) { setNote(e instanceof Error ? e.message : "Export fehlgeschlagen."); }
    finally { setBusy(false); }
  };
  const [wipe, setWipe] = useState(false);
  const wipeAll = async () => {
    setBusy(true);
    try {
      const r = await api.memoryAction("alle_loeschen", { bestaetigt: true });
      setNote(`Alle ${r.geloescht} Erinnerungen gelöscht. Chatverlauf und Dateianhänge sind davon nicht betroffen.`);
      setWipe(false); void load(query.trim());
    } catch (e) { setNote(e instanceof Error ? e.message : "Nicht gelöscht."); }
    finally { setBusy(false); }
  };
  const reindex = async () => {
    setBusy(true);
    try { const r = await api.memoryAction("index_neu"); setNote(`Suchindex neu aufgebaut aus ${r.wissen} Erinnerungen und ${r.archiv} Gesprächszügen.`); }
    catch (e) { setNote(e instanceof Error ? e.message : "Index nicht neu aufgebaut."); }
    finally { setBusy(false); }
  };
  if (!data) return <p className="cc-empty">{error ?? "Gedächtnis lädt …"}</p>;
  const z = data.zahlen;
  const searching = data.suche.trim().length > 0;
  return (
    <div className="mc">
      {error && <div className="cc-error" role="status">{error}</div>}
      <div className="mc-stats">
        <div><strong>{z.facts}</strong><span>Erinnerungen (dauerhaftes Wissen)</span></div>
        <div><strong>{z.episodes}</strong><span>Gesprächszüge im Archiv{z.archiv_seit ? ` seit ${when(z.archiv_seit).slice(0, 10)}` : ""}</span></div>
        <div className={z.ausstehend ? "attn" : ""}><strong>{z.ausstehend}</strong><span>ausstehend</span></div>
        <div className={z.fehlgeschlagen ? "warn" : ""}><strong>{z.fehlgeschlagen}</strong><span>Antwort fehlgeschlagen</span></div>
        <div className={z.widersprueche ? "warn" : ""}><strong>{z.widersprueche}</strong><span>Widersprüche offen</span></div>
      </div>
      <div className="mc-bar">
        <input type="search" className="cc-search" placeholder="Im Gedächtnis suchen (Wissen und Gespräche) …" aria-label="Im Gedächtnis suchen"
          value={query} onChange={(e) => setQuery(e.target.value)} />
        <div className="cc-tabs" role="group" aria-label="Bereich">
          <button type="button" aria-pressed={tab === "wissen"} onClick={() => setTab("wissen")}>Wissen ({data.wissen.length})</button>
          <button type="button" aria-pressed={tab === "archiv"} onClick={() => setTab("archiv")}>Gesprächsarchiv ({data.archiv.length})</button>
        </div>
        <button type="button" className="cc-btn small" disabled={busy} onClick={() => void exportAll()}>Exportieren</button>
        {canEdit && <button type="button" className="cc-btn small" disabled={busy} onClick={() => void reindex()}>Suchindex neu aufbauen</button>}
        {canEdit && z.facts > 0 && <button type="button" className="cc-btn small" disabled={busy} aria-expanded={wipe} onClick={() => setWipe(true)}>Alle Erinnerungen löschen</button>}
      </div>
      {wipe && (
        <div className="mc-confirm" role="alertdialog" aria-label="Alle Erinnerungen löschen">
          <p>Alle {z.facts} Erinnerungen endgültig löschen – samt früherer Fassungen? Das lässt sich nicht rückgängig machen.
            Der Chatverlauf (Gesprächsarchiv) und Dateianhänge bleiben.</p>
          <div className="cc-actions">
            <button type="button" className="cc-btn small mc-danger" disabled={busy} onClick={() => void wipeAll()}>Ja, alle endgültig löschen</button>
            <button type="button" className="cc-btn small" onClick={() => setWipe(false)}>Abbrechen</button>
          </div>
        </div>
      )}
      <details className="crm-more mc-explain"><summary>Was wird wo gespeichert?</summary>
        <p><strong>Erinnerungen</strong>: nur was du ausdrücklich speicherst („Merk dir, dass …“). <strong>Gesprächsarchiv</strong>: der Chatverlauf.
          <strong> Dateianhänge</strong>: nur bis zur nächsten Nachricht im Arbeitsspeicher. Jarvis nutzt je Frage nur passende Erinnerungen und zeigt an, welche.</p></details>
      {note && <div className="cc-notice mc-note" role="status">{note} <button type="button" className="cc-link" onClick={() => setNote(null)}>OK</button></div>}
      {searching && data.wissen.length === 0 && data.archiv.length === 0 && (
        <p className="cc-empty">Nichts gefunden zu „{data.suche}“. Jarvis erfindet keine Erinnerung – dazu ist nichts gespeichert.</p>
      )}
      {tab === "wissen" ? (
        <>
          {canEdit && !searching && (
            <form className="mc-add" onSubmit={(e) => { e.preventDefault(); if (draft.trim()) void add(); }}>
              <label htmlFor="mc-new">Neue Erinnerung</label>
              <div>
                <input id="mc-new" value={draft} maxLength={4000} placeholder="z. B. Mein Ziel bis März: 5 zahlende Kunden" onChange={(e) => setDraft(e.target.value)} />
                <select aria-label="Kategorie" value={category} onChange={(e) => setCategory(e.target.value)}>
                  {CATEGORIES.map((c) => <option key={c} value={c}>{CATEGORY[c] ?? c}</option>)}
                </select>
                <button type="submit" className="cc-btn accent" disabled={busy || !draft.trim()}>Speichern</button>
              </div>
            </form>
          )}
          {data.wissen.length === 0 && !searching ? (
            <p className="cc-empty">Noch keine Erinnerungen. Sag „Merk dir, dass …“ oder trag oben etwas ein.</p>
          ) : <ul className="mc-list">{data.wissen.map((f) => <FactRow key={f.id + f.updated_at} fact={f} all={data.wissen} canEdit={canEdit} onChanged={changed} />)}</ul>}
          {(data.notizdateien?.length ?? 0) > 0 && (
            <section className="cc-card-box" aria-label="Arbeitsnotizen der Agenten">
              <header><h3>Arbeitsnotizen der Agenten (Dateien)</h3><span className="cc-tag">{data.notizdateien!.length}</span></header>
              <p className="cc-hint" style={{ marginTop: 0 }} title="Seit Memory v1 legt das Modell dort nichts mehr ab, und die Dateien gehen nicht mehr an das Modell. Löschen: Datei im Finder entfernen.">
                Alt, nicht mehr in Gebrauch: <span className="cc-mono">~/.jarvis/memories</span></p>
              <ul className="mc-list">{data.notizdateien!.map((n) => (
                <li key={n.name} className="mc-turn"><div className="mc-meta"><span className="cc-mono">{n.name}</span><span>geändert {when(n.geaendert)}</span><span>{n.groesse} Bytes</span></div>
                  <p className="mc-jarvis">{n.auszug}</p></li>))}</ul>
            </section>
          )}
        </>
      ) : data.archiv.length === 0 && !searching ? (
        <p className="cc-empty">Das Gesprächsarchiv ist leer.</p>
      ) : <ul className="mc-list">{data.archiv.map((e) => <ArchiveRow key={e.id} item={e} canEdit={canEdit} onChanged={changed} />)}</ul>}
      <details className="crm-more"><summary>Speicherort und Schutz · Stand {when(data.stand)}</summary>
        <p>Quelle: <span className="cc-mono">{z.path}</span> (nur auf diesem Mac). Gespeichert wird nur Text, kein Roh-Audio. Zugangsdaten werden vor dem Speichern geschwärzt oder abgelehnt.
          Der Suchindex ist nie die einzige Kopie und lässt sich neu aufbauen.</p></details>
    </div>
  );
}
