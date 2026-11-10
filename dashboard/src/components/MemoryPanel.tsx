import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export type MemoryFact = {
  id: string;
  content: string;
  category: string;
  updated_at: string;
};
export type MemoryEpisode = {
  id: string;
  conversation_id: string;
  user_text: string;
  assistant_text: string;
  status: string;
  created_at: string;
};
export type MemoryEvent = { id?: string; type?: string; action?: string; timestamp?: string; [key: string]: unknown };
export type MemorySnapshot = {
  facts: MemoryFact[];
  episodes: MemoryEpisode[];
  stats: { facts: number; episodes: number };
  events: MemoryEvent[];
};

const MAX_ITEMS = 50;
const CATEGORIES = ["preference", "project", "note"] as const;
const SENSITIVE_MEMORY = /(?:password|passwort|secret|api[ _-]?key|token|bearer|private[ _-]?key|sk-[a-z0-9]|-----begin)/i;

export function isSensitiveMemoryText(content: string): boolean {
  return SENSITIVE_MEMORY.test(content);
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}
function boundedArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]).slice(0, MAX_ITEMS) : [];
}

/** Accepts the V3 contract and harmless singular/legacy aliases from either backend. */
export function normalizeMemorySnapshot(value: unknown): MemorySnapshot {
  const raw = value && typeof value === "object" ? (value as Record<string, unknown>) : {};
  const facts = boundedArray<Record<string, unknown>>(raw.facts ?? raw.fact).map((fact, index) => ({
    id: asText(fact.id || `fact-${index}`),
    content: asText(fact.content ?? fact.text),
    category: asText(fact.category || "note"),
    updated_at: asText(fact.updated_at ?? fact.updatedAt ?? fact.created_at),
  }));
  const episodes = boundedArray<Record<string, unknown>>(raw.episodes ?? raw.episode).map((episode, index) => ({
    id: asText(episode.id || `episode-${index}`),
    conversation_id: asText(episode.conversation_id ?? episode.conversationId),
    user_text: asText(episode.user_text ?? episode.userText),
    assistant_text: asText(episode.assistant_text ?? episode.assistantText),
    status: asText(episode.status),
    created_at: asText(episode.created_at ?? episode.createdAt),
  }));
  const stats = raw.stats && typeof raw.stats === "object" ? (raw.stats as Record<string, unknown>) : {};
  const count = (value: unknown, fallback: number) => {
    const number = Number(value);
    return Number.isFinite(number) && number >= 0 ? Math.min(MAX_ITEMS, Math.floor(number)) : fallback;
  };
  return {
    facts,
    episodes,
    stats: { facts: count(stats.facts, facts.length), episodes: count(stats.episodes, episodes.length) },
    events: boundedArray<MemoryEvent>(raw.events ?? raw.event),
  };
}

export function filterMemoryFacts(facts: MemoryFact[], query: string): MemoryFact[] {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return facts;
  return facts.filter((fact) => `${fact.content} ${fact.category}`.toLocaleLowerCase().includes(needle));
}

function emitMemoryEvent() {
  window.dispatchEvent(new CustomEvent("memorychange"));
}

function useMemorySnapshot() {
  const [snapshot, setSnapshot] = useState<MemorySnapshot>({ facts: [], episodes: [], stats: { facts: 0, episodes: 0 }, events: [] });
  const [error, setError] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const load = useCallback(async () => {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    try {
      const response = await fetch("/api/memory", { headers: { "X-Jarvis": "1" }, signal: controller.signal, credentials: "same-origin" });
      if (!response.ok) throw new Error(`Memory service unavailable (${response.status})`);
      const next = normalizeMemorySnapshot(await response.json());
      if (mountedRef.current) { setSnapshot(next); setError(null); }
    } catch (reason) {
      if (!mountedRef.current || (reason instanceof DOMException && reason.name === "AbortError")) return;
      setError(reason instanceof Error ? reason.message : "Memory service unavailable");
    }
  }, []);
  useEffect(() => {
    mountedRef.current = true;
    let timer: number | undefined;
    const schedule = () => {
      if (timer !== undefined) window.clearInterval(timer);
      timer = document.hidden ? undefined : window.setInterval(() => void load(), 5000);
    };
    const onVisibility = () => { if (!document.hidden) void load(); schedule(); };
    const onMemoryEvent = () => void load();
    void load();
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("memorychange", onMemoryEvent);
    window.addEventListener("jarvis:memory", onMemoryEvent);
    schedule();
    return () => {
      mountedRef.current = false;
      controllerRef.current?.abort();
      if (timer !== undefined) window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("memorychange", onMemoryEvent);
      window.removeEventListener("jarvis:memory", onMemoryEvent);
    };
  }, [load]);
  return { snapshot, error, reload: load };
}

export function MemoryPanel({ compact = false, room }: { compact?: boolean; room?: string }) {
  const { snapshot, error, reload } = useMemorySnapshot();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [content, setContent] = useState("");
  const [category, setCategory] = useState<(typeof CATEGORIES)[number]>("note");
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const debounceRef = useRef<number | undefined>(undefined);
  useEffect(() => {
    if (debounceRef.current !== undefined) window.clearTimeout(debounceRef.current);
    debounceRef.current = window.setTimeout(() => setQuery(search), 250);
    return () => { if (debounceRef.current !== undefined) window.clearTimeout(debounceRef.current); };
  }, [search]);
  const facts = useMemo(() => filterMemoryFacts(snapshot.facts, query), [snapshot.facts, query]);
  const opsHref = `/?screen=ops${room ? `&room=${encodeURIComponent(room)}` : ""}#memory`;
  const mutate = async (request: RequestInit, message: string) => {
    setBusy(true); setActionError(null);
    try {
      const response = await fetch("/api/memory", { ...request, headers: { "Content-Type": "application/json", "X-Jarvis": "1", ...(request.headers || {}) }, credentials: "same-origin" });
      if (!response.ok) throw new Error(`${message} (${response.status})`);
      emitMemoryEvent();
      await reload();
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : message);
    } finally { setBusy(false); }
  };
  const create = async () => {
    const value = content.trim();
    if (!value) return setActionError("Enter a memory note first.");
    if (isSensitiveMemoryText(value)) return setActionError("Not saved: memory must not contain passwords, tokens, or API keys.");
    await mutate({ method: "POST", body: JSON.stringify({ content: value, category }) }, "Memory note was not saved");
    setContent("");
  };
  const deleteFact = async (id: string) => {
    if (!window.confirm("Delete this memory note?")) return;
    setBusy(true); setActionError(null);
    try {
      const response = await fetch(`/api/memory/${encodeURIComponent(id)}`, { method: "DELETE", headers: { "X-Jarvis": "1" }, credentials: "same-origin" });
      if (!response.ok) throw new Error(`Memory note was not deleted (${response.status})`);
      emitMemoryEvent(); await reload();
    } catch (reason) { setActionError(reason instanceof Error ? reason.message : "Memory note was not deleted"); }
    finally { setBusy(false); }
  };
  if (compact) return (
    <section className="memory-compact" aria-label="Memory summary">
      <div className="eyebrow">04 / SHARED MEMORY</div>
      <div className="memory-counts"><strong>{snapshot.stats.facts}</strong><span>FACTS</span><strong>{snapshot.stats.episodes}</strong><span>EPISODES</span></div>
      <a className="btn compact" href={opsHref}>OPEN OPS MEMORY</a>
      {error && <div className="memory-error" role="status">{error}</div>}
    </section>
  );
  return (
    <section className="memory-panel" id="memory" aria-label="Memory management">
      <div className="memory-header"><div><div className="eyebrow">SHARED MEMORY / V3 CONTRACT</div><h3>FACTS &amp; EPISODES</h3></div><button type="button" className="btn compact" onClick={() => void reload()} disabled={busy}>REFRESH</button></div>
      <div className="memory-stats"><span>{snapshot.stats.facts} facts</span><span>{snapshot.stats.episodes} episodes</span><span>{snapshot.events.length} events</span></div>
      <div className="memory-form"><input value={content} maxLength={2000} placeholder="Store a non-sensitive note …" aria-label="Memory note" onChange={(event) => setContent(event.target.value)} /><select aria-label="Memory category" value={category} onChange={(event) => setCategory(event.target.value as (typeof CATEGORIES)[number])}>{CATEGORIES.map((item) => <option key={item} value={item}>{item}</option>)}</select><button type="button" className="btn amber" onClick={() => void create()} disabled={busy}>CREATE NOTE</button></div>
      <input className="memory-search" value={search} placeholder="Search facts …" aria-label="Search facts" onChange={(event) => setSearch(event.target.value)} />
      {error && <div className="memory-error" role="status">{error}</div>}{actionError && <div className="memory-error" role="alert">{actionError}</div>}
      <div className="memory-facts">{facts.length === 0 ? <div className="empty">NO MATCHING FACTS</div> : facts.map((fact) => <article className="memory-fact" key={fact.id}><div><span className="memory-category">{fact.category}</span><p>{fact.content}</p><small>{fact.updated_at || "—"}</small></div><button type="button" className="btn danger compact" onClick={() => void deleteFact(fact.id)} disabled={busy}>DELETE</button></article>)}</div>
      <div className="memory-note">Episodes are read-only here. Text is rendered as plain text; no HTML or hidden reasoning is stored or displayed.</div>
    </section>
  );
}
