import { Suspense, lazy, useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { api, ApiError, type Approval, type Automations, type CalendarData, type CalendarEvent, type Column, type Job, type Order, type OrderStatus } from "../command/api";
import { boardReducer, dayKey, initialBoard, timeRange } from "../command/model";
import { KanbanPanel } from "../command/KanbanPanel";
import { CallsPanel } from "../command/CallsPanel";
import { ContentPanel } from "../command/ContentPanel";
import { ApprovalsPanel } from "../command/ApprovalsPanel";
import { AgendaPanel } from "../command/AgendaPanel";
import { AutomationsPanel } from "../command/AutomationPanels";
import { AgentCenter } from "../command/AgentCenter";
import { TodayView } from "../command/TodayView";
import { buildQueue, buildSuggestions, type QueueItem } from "../command/today";
import { MemoryCenter } from "../command/MemoryCenter";
import { ConnectionsPanel } from "../command/ConnectionsPanel";
import { SkillPlatform } from "../command/SkillPlatform";
import { EventDetail } from "../command/EventDetail";
import { CommandCenter } from "../command/CommandCenter";
import { Telemetry } from "../command/telemetry";
import { LiveCamera, useBusEvents, type CameraInfo } from "../command/LiveCamera";
import type { RealtimeSession, SaveState } from "../command/realtime";
import { DEFAULT_PREFS, type VoicePrefs } from "../command/SettingsDialog";
import type { AgentCenterData, AgentRole, CallOverview, Connections, ContentOverview, CapabilityRun, Platform, Settings } from "../command/api";
import { Icon } from "../command/icons";
import type { ContentView } from "../view/intent";
import type { AudioEngine } from "../audio";
import type { Role, Status, Transcript } from "../types";
import "../command/command.css";

const CalendarPanel = lazy(() => import("../command/CalendarPanel"));
const MapboxWorld = lazy(() => import("../world/MapboxWorld"));

export type CommandSection =
  | "uebersicht" | "auftraege" | "kalender" | "termin" | "anrufe" | "content" | "agenten" | "automatisierungen" | "gedaechtnis"
  | "verbindungen" | "welt" | "gespraech" | "freigaben";
type NavItem = { id: CommandSection; label: string; icon: string };
/** Navigation folgt dem Kernablauf: erst Lage und Übergabe, dann die Arbeitsbereiche, zuletzt das System. */
const NAV: { title: string; items: NavItem[] }[] = [
  { title: "Jarvis", items: [
    { id: "uebersicht", label: "Heute", icon: "overview" },
    { id: "gespraech", label: "Command Center", icon: "chat" },
    { id: "freigaben", label: "Freigaben", icon: "approvals" },
    { id: "agenten", label: "Agenten", icon: "agents" },
  ] },
  { title: "Arbeit", items: [
    { id: "auftraege", label: "Aufträge", icon: "orders" },
    { id: "kalender", label: "Kalender", icon: "calendar" },
    { id: "anrufe", label: "Vertrieb", icon: "phone" },
    { id: "content", label: "Content", icon: "news" },
  ] },
  { title: "System", items: [
    { id: "automatisierungen", label: "Skills und Automationen", icon: "automation" },
    { id: "gedaechtnis", label: "Gedächtnis", icon: "memory" },
    { id: "verbindungen", label: "Verbindungen", icon: "link" },
  ] },
];
/** Mobile Leiste: höchstens fünf Ziele, der Rest liegt im Menü. */
const TABS: NavItem[] = NAV[0].items;
const TITLES: Record<CommandSection, string> = {
  uebersicht: "Heute", auftraege: "Aufträge", kalender: "Kalender", termin: "Termin", anrufe: "Vertrieb", content: "Content", agenten: "Agenten",
  automatisierungen: "Skills und Automationen", gedaechtnis: "Gedächtnis", verbindungen: "Verbindungen und Einrichtung",
  welt: "World View", gespraech: "Command Center", freigaben: "Freigaben",
};
const MORE: { id: ContentView | "welt"; label: string; icon: string }[] = [
  { id: "welt", label: "World View", icon: "world" },
  { id: "news", label: "Nachrichten", icon: "news" },
  { id: "market", label: "Märkte", icon: "market" },
  { id: "cameras", label: "Kameras", icon: "camera" },
  { id: "ops", label: "Systemprotokoll", icon: "system" },
];
const DEFAULT_COLUMNS: Column[] = [
  { id: "eingang", titel: "Eingang" }, { id: "klaerung", titel: "In Klärung" }, { id: "geplant", titel: "Geplant" },
  { id: "in_arbeit", titel: "In Arbeit" }, { id: "freigabe", titel: "Prüfung" }, { id: "erledigt", titel: "Erledigt" },
];
// Spaltennamen im Dashboard (Status-IDs im Backend bleiben unverändert).
const COLUMN_TITLES: Partial<Record<OrderStatus, string>> = { freigabe: "Prüfung" };

const ALIASES: [RegExp, CommandSection][] = [
  [/auftr|kanban|board/, "auftraege"], [/kalender|termin/, "kalender"], [/agenda/, "kalender"], [/anruf|telefon|vertrieb|lead|pipeline|follow/, "anrufe"], [/content|video|hook|tiktok|reel|shorts/, "content"], [/agent|job/, "agenten"],
  [/automat|ablauf|abläuf|workflow|n8n|skill/, "automatisierungen"], [/ged[aä]chtnis|erinner|memory/, "gedaechtnis"],
  [/verbind|einricht|integration|dienst/, "verbindungen"], [/welt|karte|world|map/, "welt"],
  [/gespr|chat|coach|command center|sprach/, "gespraech"], [/freigab/, "freigaben"], [/übersicht|uebersicht|heute|start|home/, "uebersicht"],
];
export function sectionFromQuery(query?: string): CommandSection {
  const q = (query || "").toLowerCase();
  return ALIASES.find(([re]) => re.test(q))?.[1] ?? "uebersicht";
}

type Selected = { kind: "order"; order: Order } | null;

export type WorkspaceProps = {
  query?: string;
  section?: CommandSection;
  worldQuery?: string;
  role: Role;
  connected: boolean;
  onAsk: (text: string) => void;
  onView: (view: ContentView) => void;
  onBack?: () => void;
  transcripts?: Transcript[];
  status?: Status;
  audio?: AudioEngine | null;
  micOn?: boolean;
  onMicChange?: (on: boolean) => void;
  onLog?: (m: string, l?: "info" | "warn") => void;
  onStop?: () => void;
  telemetry?: Telemetry;
  muted?: boolean;
  onMutedChange?: (muted: boolean) => void;
  voiceMode?: "studio" | "echtzeit";
  transcriptSave?: SaveState;
  onRetryTranscripts?: () => void;
  onStartRealtime?: () => Promise<void>;
  onStopRealtime?: () => Promise<void>;
  getRealtime?: () => RealtimeSession | null;
  voicePrefs?: VoicePrefs;
  onVoicePrefs?: (prefs: VoicePrefs) => void;
  onReset?: () => void;
  /** Raum = Gespräch; nötig für Dateianhänge. */
  room?: string;
};

export default function CommandView({
  query, section: forced, worldQuery, role, connected, onAsk, onView,
  transcripts = [], audio = null, micOn = false, onMicChange = () => undefined, onLog = () => undefined, onStop = () => undefined,
  telemetry: givenTelemetry, muted = false, onMutedChange = () => undefined,
  voiceMode = "studio", transcriptSave, onRetryTranscripts = () => undefined, onStartRealtime = async () => undefined, onStopRealtime = async () => undefined, getRealtime = () => null,
  voicePrefs = DEFAULT_PREFS, onVoicePrefs = () => undefined, onReset = () => undefined, room,
}: WorkspaceProps) {
  const [ownTelemetry] = useState(() => new Telemetry());
  const telemetry = givenTelemetry ?? ownTelemetry;
  const [section, setSection] = useState<CommandSection>(() => forced ?? sectionFromQuery(query));
  useEffect(() => { if (forced) setSection(forced); }, [forced, worldQuery]);
  useEffect(() => { if (query) setSection(sectionFromQuery(query)); }, [query]);
  const [navOpen, setNavOpen] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const [ready, setReady] = useState(false);
  const [focusComposer, setFocusComposer] = useState(0);
  const [calTab, setCalTab] = useState<"monat" | "liste">("monat");
  const [search, setSearch] = useState("");
  const [board, dispatch] = useReducer(boardReducer, initialBoard);
  const [columns, setColumns] = useState<Column[]>(DEFAULT_COLUMNS);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [calendar, setCalendar] = useState<CalendarData | null>(null);
  const [range, setRange] = useState<{ von: string; bis: string } | null>(null);
  const [automations, setAutomations] = useState<Automations | null>(null);
  const [automationError, setAutomationError] = useState<string | null>(null);
  const [offline, setOffline] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selected, setSelected] = useState<Selected>(null);
  const [event, setEvent] = useState<CalendarEvent | null>(null);
  const canEdit = role === "owner";
  const [settings, setSettings] = useState<Settings | null>(null);
  const [camera, setCamera] = useState<CameraInfo | null>(null);
  const [platform, setPlatform] = useState<Platform | null>(null);
  const [calls, setCalls] = useState<CallOverview | null>(null);
  const [callsError, setCallsError] = useState<string | null>(null);
  const [contentData, setContentData] = useState<ContentOverview | null>(null);
  const [contentError, setContentError] = useState<string | null>(null);
  const [capError, setCapError] = useState<string | null>(null);
  const [capBusy, setCapBusy] = useState<string | null>(null);
  const [agentData, setAgentData] = useState<AgentCenterData | null>(null);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [focusJob, setFocusJob] = useState<string | null>(null);
  const [connections, setConnections] = useState<Connections | null>(null);
  const [connError, setConnError] = useState<string | null>(null);
  const [connBusy, setConnBusy] = useState(false);
  const alive = useRef(true);
  useEffect(() => () => void (alive.current = false), []);

  const go = useCallback((next: CommandSection) => {
    setSection(next);
    setNavOpen(false);
  }, []);
  const openEvent = useCallback((e: CalendarEvent) => {
    setEvent(e);
    go("termin");
  }, [go]);

  const loadOrders = useCallback(async () => {
    try {
      const data = await api.orders();
      if (!alive.current) return;
      if (!Array.isArray(data?.auftraege)) throw new Error("Unerwartete Antwort vom Jarvis-Server.");
      const cols = (Array.isArray(data.spalten) && data.spalten.length ? data.spalten : DEFAULT_COLUMNS)
        .map((c) => ({ ...c, titel: COLUMN_TITLES[c.id] ?? c.titel }));
      setColumns(cols);
      dispatch({ type: "load", orders: data.auftraege });
      setOffline(null);
    } catch (error) {
      if (alive.current) setOffline(error instanceof ApiError ? error.message : "Jarvis-Server nicht erreichbar.");
    }
  }, []);
  const [callDays, setCallDays] = useState(7);
  const loadCalls = useCallback(async (days = callDays) => {
    try {
      const data = await api.calls(days);
      if (!alive.current) return;
      if (!data || !Array.isArray(data.projekte) || !data.kennzahlen) throw new Error("Unerwartete Antwort vom Anruf-Speicher.");
      setCalls(data);
      setCallsError(null);
    } catch (error) {
      if (alive.current) setCallsError(error instanceof ApiError ? error.message : error instanceof Error ? error.message : "Anrufe nicht erreichbar.");
    }
  }, [callDays]);
  useEffect(() => { if (section === "anrufe" || section === "uebersicht") void loadCalls(); }, [section, loadCalls]);
  const loadContent = useCallback(async () => {
    try {
      const data = await api.content();
      if (!alive.current) return;
      if (!data || !Array.isArray(data.eintraege) || !Array.isArray(data.spalten)) throw new Error("Unerwartete Antwort vom Content-Speicher.");
      setContentData(data);
      setContentError(null);
    } catch (error) {
      if (alive.current) setContentError(error instanceof Error ? error.message : "Content nicht erreichbar.");
    }
  }, []);
  useEffect(() => { if (section === "content" || section === "uebersicht") void loadContent(); }, [section, loadContent]);
  const moveContent = useCallback(async (id: string, status: string) => {
    try { await api.setContentStatus(id, status); await loadContent(); }
    catch (error) { if (alive.current) setNotice(error instanceof Error ? error.message : "Status nicht geändert."); }
  }, [loadContent]);
  const loadApprovals = useCallback(async () => {
    try {
      const data = await api.approvals();
      if (alive.current && Array.isArray(data?.freigaben)) setApprovals(data.freigaben);
    } catch { /* letzter Stand bleibt sichtbar */ }
  }, []);
  const loadCalendar = useCallback(async (von = range?.von ?? "", bis = range?.bis ?? "") => {
    try {
      const data = await api.calendar(von, bis);
      if (!alive.current) return;
      setCalendar(data && Array.isArray(data.termine)
        ? { ...data, konflikte: Array.isArray(data.konflikte) ? data.konflikte : [] }
        : { status: "fehler", hinweis: "Unerwartete Antwort vom Kalender.", termine: [], konflikte: [] });
    } catch (error) {
      if (alive.current)
        setCalendar((old) => old ?? { status: "offline", hinweis: error instanceof Error ? error.message : "offline", termine: [], konflikte: [] });
    }
  }, [range]);
  const loadWork = useCallback(async () => {
    const [a] = await Promise.allSettled([api.automations()]);
    if (!alive.current) return;
    if (a.status === "fulfilled" && Array.isArray(a.value?.agenten) && Array.isArray(a.value?.ablaeufe)) {
      setAutomations(a.value);
      setAutomationError(null);
    }
    else setAutomationError(a.status === "rejected" && a.reason instanceof ApiError && a.reason.status === 404
      ? "Der Jarvis-Server ist älter als dieses Dashboard (Endpunkt /api/automations fehlt) – Jarvis neu starten."
      : "Status von Agenten und Automatisierungen nicht erreichbar.");
    try {
      const data = await api.agentCenter();
      if (!alive.current) return;
      if (!Array.isArray(data?.jobs) || !Array.isArray(data?.rollen)) throw new Error("Unerwartete Antwort");
      setAgentData(data);
      setAgentError(null);
    } catch (error) {
      if (alive.current) setAgentError(error instanceof ApiError && error.status === 404
        ? "Der Jarvis-Server ist älter als dieses Dashboard (/api/agenten fehlt) – Jarvis neu starten."
        : "Agenten-Center nicht erreichbar – läuft der Jarvis-Server?");
    }
  }, []);
  const loadConnections = useCallback(async (fresh = false) => {
    setConnBusy(true);
    try {
      const data = await api.connections(fresh);
      if (!alive.current) return;
      if (!Array.isArray(data?.verbindungen)) throw new Error("Unerwartete Antwort");
      setConnections(data);
      setConnError(null);
    } catch (error) {
      if (alive.current) setConnError(error instanceof ApiError && error.status === 404
        ? "Der Jarvis-Server ist älter als dieses Dashboard (/api/verbindungen fehlt) – Jarvis neu starten."
        : "Verbindungen nicht prüfbar – läuft der Jarvis-Server?");
    } finally { if (alive.current) setConnBusy(false); }
  }, []);
  useEffect(() => { if (section === "verbindungen" || section === "uebersicht") void loadConnections(); }, [section, loadConnections]);

  const loadSettings = useCallback(async () => {
    try {
      const data = await api.settings();
      if (alive.current && Array.isArray(data?.stimmen)) setSettings(data);
    } catch { /* Kopfzeile zeigt dann „–“ */ }
  }, []);
  useEffect(() => { void loadSettings(); }, [loadSettings]);
  const loadCapabilities = useCallback(async () => {
    try {
      const data = await api.capabilities();
      if (!alive.current) return;
      if (!Array.isArray(data?.faehigkeiten)) throw new Error("Unerwartete Antwort");
      setPlatform({ ...data, laeufe: data.laeufe ?? [], skills: data.skills ?? [] });
      setCapError(null);
    } catch (error) {
      if (alive.current) setCapError(error instanceof ApiError && error.status === 404
        ? "Der Jarvis-Server ist älter als dieses Dashboard (/api/faehigkeiten fehlt) – Jarvis neu starten."
        : "Fähigkeiten nicht erreichbar – läuft der Jarvis-Server?");
    }
  }, []);
  useEffect(() => { void loadCapabilities(); }, [loadCapabilities]);
  const startCapability = useCallback(async (name: string, args: Record<string, unknown>) => {
    const result = await api.startCapability(name, args);
    if (result?.lauf) setPlatform((old) => old && { ...old, laeufe: [result.lauf, ...old.laeufe.filter((r) => r.id !== result.lauf.id)] });
  }, []);
  /** Rolle/Skill als echten Job starten (Fähigkeit skill_starten): eigenes Gespräch, eigener Werkzeug-Rahmen. */
  const startSkill = useCallback(async (skill: string, task: string) => {
    await api.startCapability("skill_starten", { skill, auftrag: task });
    void loadCapabilities();
    void loadWork();
  }, [loadCapabilities, loadWork]);
  const startRole = useCallback(async (role: AgentRole, task: string) => {
    if (!role.skill) throw new Error("Diese Rolle hat keine startbare Skill.");
    await startSkill(role.skill, task);
  }, [startSkill]);
  const cancelJob = useCallback(async (id: string) => {
    setCapBusy(id);
    try {
      const result = await api.cancelJob(id);
      if (!result.abgebrochen) setNotice("Der Job war bereits beendet – nichts abgebrochen.");
    } catch (error) {
      setNotice(`Abbrechen nicht möglich: ${error instanceof Error ? error.message : "unbekannt"}`);
    } finally { setCapBusy(null); void loadWork(); }
  }, [loadWork]);
  const openJob = useCallback((id?: string) => { setFocusJob(id ?? null); go("agenten"); }, [go]);
  const toggleSkill = useCallback(async (id: string, active: boolean) => {
    try {
      await api.setSkillActive(id, active);
    } catch (error) {
      setNotice(`Skill nicht umgeschaltet: ${error instanceof Error ? error.message : "unbekannt"}`);
    }
    void loadCapabilities();
  }, [loadCapabilities]);
  const capabilityAction = useCallback(async (id: string, action: "abbrechen" | "wiederholen") => {
    setCapBusy(id);
    try {
      await api.capabilityAction(id, action);
    } catch (error) {
      setNotice(`${action === "abbrechen" ? "Abbrechen" : "Wiederholen"} nicht möglich: ${error instanceof Error ? error.message : "unbekannt"}`);
    } finally {
      setCapBusy(null);
      void loadCapabilities();
      void loadWork();
    }
  }, [loadCapabilities, loadWork]);
  // Ereignis-Bus: Live-Kameras (Werkzeug live_kamera) und Job-Fortschritt, auch aus dem Echtzeit-Modus.
  const jobTimer = useRef<number | undefined>(undefined);
  useBusEvents(connected, (ev) => {
    if (ev.typ === "kamera") setCamera(ev.aktion === "schliessen" ? null : (ev.kamera ?? null));
    else if (ev.typ === "faehigkeit" && ev.lauf && typeof ev.lauf.id === "string") {
      const run = ev.lauf as CapabilityRun;
      setPlatform((old) => old && { ...old, laeufe: [run, ...old.laeufe.filter((r) => r.id !== run.id)].sort((a, b) => b.start - a.start).slice(0, 25) });
      if (ev.phase === "fertig") void loadOrders(); // neuer Auftrag sofort im Kanban
    } else {
      window.clearTimeout(jobTimer.current);
      jobTimer.current = window.setTimeout(() => { void loadWork(); void loadApprovals(); }, 300);
    }
  });

  useEffect(() => {
    void Promise.allSettled([loadOrders(), loadApprovals()]).then(() => { if (alive.current) setReady(true); });
    void loadWork();
    const fast = window.setInterval(() => void loadApprovals(), 10_000);
    const slow = window.setInterval(() => { void loadOrders(); void loadWork(); }, 30_000);
    return () => { window.clearInterval(fast); window.clearInterval(slow); };
  }, [loadOrders, loadApprovals, loadWork]);
  useEffect(() => {
    void loadCalendar();
    const t = window.setInterval(() => void loadCalendar(), 120_000);
    return () => window.clearInterval(t);
  }, [loadCalendar]);

  const move = useCallback(async (order: Order, next: OrderStatus, position: number) => {
    if (!canEdit) return;
    dispatch({ type: "move", id: order.id, status: next, position });
    try {
      const result = await api.updateOrder(order, { status: next, position });
      dispatch({ type: "confirm", order: result.auftrag });
    } catch (error) {
      const current = error instanceof ApiError && error.status === 409 ? (error.body?.aktuell as Order) : undefined;
      dispatch({
        type: "rollback", id: order.id, current,
        message: current ? "Der Auftrag wurde inzwischen geändert – aktueller Stand geladen." :
          `Verschieben fehlgeschlagen, zurückgesetzt (${error instanceof Error ? error.message : "unbekannt"}).`,
      });
    }
  }, [canEdit]);
  const create = useCallback(async (fields: Partial<Order>) => {
    try {
      const result = await api.createOrder(fields);
      dispatch({ type: "add", order: result.auftrag });
    } catch (error) {
      setNotice(`Nicht angelegt: ${error instanceof Error ? error.message : "unbekannt"}`);
    }
  }, []);
  const decide = useCallback(async (id: string, action: "freigeben" | "verwerfen") => {
    try {
      const result = await api.decide(id, action);
      setNotice(action === "freigeben" ? `Ausgeführt: ${result.ergebnis}` : "Verworfen.");
    } catch (error) {
      setNotice(`Nicht möglich: ${error instanceof Error ? error.message : "unbekannt"}`);
    }
    void loadApprovals();
    void loadOrders();
  }, [loadApprovals, loadOrders]);
  const openOrder = useCallback(async (id: string) => {
    let order = board.orders.find((o) => o.id === id);
    if (!order) {
      try {
        const data = await api.orders();
        dispatch({ type: "load", orders: data.auftraege });
        order = data.auftraege.find((o) => o.id === id);
      } catch { /* Kanban zeigt dann den letzten Stand */ }
    }
    if (order) setSelected({ kind: "order", order });
    go("auftraege");
  }, [board.orders, go]);
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        go("gespraech");
        setFocusComposer((n) => n + 1);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [go]);
  const ask = useCallback((text: string) => {
    onAsk(text);
    go("gespraech");
  }, [onAsk, go]);

  const openApprovals = approvals.filter((a) => a.status === "WAITING_FOR_APPROVAL");
  const tz = calendar?.zeitzone || "Europe/Berlin";
  const today = dayKey(new Date(), tz);
  const runningJobs = (agentData?.jobs ?? []).filter((j) => j.status === "RUNNING" || j.status === "WAITING_FOR_APPROVAL");
  const next = (calendar?.termine ?? []).find((e) => e.status !== "cancelled" && e.beginn && new Date(e.beginn).getTime() > Date.now());
  const calendarOk = calendar?.status === "ok";
  const webcams = (automations?.integrationen.find((i) => i.id === "windy")?.status ?? null) as "bereit" | "nicht_eingerichtet" | null;
  const needsYou = (connections?.verbindungen ?? []).filter((c) => ["abgelaufen", "unvollstaendig", "fehler"].includes(c.zustand));

  // „Heute“: eine priorisierte Liste aus allen Quellen; nicht erreichbare Quellen werden benannt statt verschwiegen.
  const queue = useMemo(() => buildQueue({
    approvals, orders: board.orders, today, calls, content: contentData, calendar, automations, connections, agents: agentData,
  }), [approvals, board.orders, today, calls, contentData, calendar, automations, connections, agentData]);
  const nextEvent = next ? { titel: next.titel, zeit: `${dayKey(new Date(next.beginn!), tz) === today ? "heute" : new Date(next.beginn!).toLocaleDateString("de-DE", { timeZone: tz, weekday: "long" })} ${timeRange(next, tz).split("–")[0]}` } : null;
  const suggestions = useMemo(() => buildSuggestions({ orders: board.orders, today, calls, nextEvent }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [board.orders, today, calls, nextEvent?.titel, nextEvent?.zeit]);
  const unchecked = [
    offline ? "Aufträge" : null, !calls && callsError ? "Vertrieb" : null, !contentData && contentError ? "Content" : null,
    !agentData && agentError ? "Agenten" : null, !automations && automationError ? "Abläufe" : null, !connections && connError ? "Verbindungen" : null,
  ].filter((x): x is string => !!x);
  const openItem = (item: QueueItem) => {
    if (item.orderId) return void openOrder(item.orderId);
    if (item.jobId) return openJob(item.jobId);
    go(item.target);
  };

  const badge = (id: CommandSection) =>
    id === "freigaben" && openApprovals.length > 0 ? <span className="cc-badge" aria-label={`${openApprovals.length} offen`}>{openApprovals.length}</span>
    : id === "agenten" && runningJobs.length > 0 ? <span className="cc-badge live" aria-label={`${runningJobs.length} aktiv`}>{runningJobs.length}</span>
    : id === "verbindungen" && needsYou.length > 0 ? <span className="cc-badge" aria-label={`${needsYou.length} brauchen dich`}>{needsYou.length}</span>
    : null;
  const isCurrent = (id: CommandSection) => section === id || (id === "kalender" && section === "termin");
  const navButton = (s: NavItem) => (
    <button key={s.id} type="button" className={isCurrent(s.id) ? "active" : ""}
      aria-current={isCurrent(s.id) ? "page" : undefined} onClick={() => go(s.id)}>
      <Icon name={s.icon} /> {s.label}
      {badge(s.id)}
    </button>
  );
  const moreVisible = moreOpen || section === "welt";
  const searchable = ["auftraege", "kalender", "freigaben"].includes(section);

  return (
    <section className="cc" aria-label="Jarvis Arbeitsbereich" data-section={section}>
      <nav className={`cc-nav ${navOpen ? "open" : ""}`} aria-label="Hauptnavigation">
        <div className="cc-brand"><span className="cc-logo" aria-hidden="true" /><span>Jarvis</span></div>
        {NAV.map((group) => (
          <div key={group.title} className="cc-nav-group" role="group" aria-label={group.title}>
            <div className="cc-nav-more">{group.title}</div>
            {group.items.map(navButton)}
          </div>
        ))}
        <div className="cc-nav-group" role="group" aria-label="Mehr">
          <button type="button" className="cc-nav-toggle" aria-expanded={moreVisible} onClick={() => setMoreOpen((v) => !v)}>
            <span>Mehr</span><span aria-hidden="true">{moreVisible ? "–" : "+"}</span>
          </button>
          {moreVisible && MORE.map((m) => (
            <button key={m.id} type="button" className={m.id === "welt" && section === "welt" ? "active" : ""}
              onClick={() => { if (m.id === "welt") go("welt"); else { setNavOpen(false); onView(m.id); } }}><Icon name={m.icon} /> {m.label}</button>
          ))}
        </div>
        <div className={`cc-nav-foot ${connected ? "ok" : "warn"}`} role="status">
          <i aria-hidden="true" /><span>{connected ? "Mit Jarvis verbunden" : "Keine Verbindung zu Jarvis"}</span>
        </div>
      </nav>

      {navOpen && <button type="button" className="cc-backdrop" aria-label="Navigation schließen" onClick={() => setNavOpen(false)} />}
      <div className="cc-main">
        <header className="cc-top">
          <button type="button" className="cc-btn small cc-menu-btn" aria-label="Navigation öffnen" aria-expanded={navOpen} onClick={() => setNavOpen((v) => !v)}>
            <Icon name="menu" />
          </button>
          <h1>{TITLES[section]}</h1>
          {searchable && (
            <input type="search" className="cc-search" placeholder="Suchen in Aufträgen, Terminen, Freigaben …" aria-label="Suchen"
              value={search} onChange={(e) => setSearch(e.target.value)} />
          )}
          {/* Status nur bei Abweichung: eine ruhige Kopfzeile heißt „alles in Ordnung“. */}
          <div className="cc-pills">
            {!connected && <span className="cc-pill warn">Getrennt</span>}
            {calendar && !calendarOk && calendar.status !== "demo" && <button type="button" className="cc-pill warn" onClick={() => go("kalender")}>Kalender nicht verfügbar</button>}
            {transcriptSave && (voiceMode === "echtzeit" || transcriptSave.ausstehend + transcriptSave.fehlgeschlagen > 0) && (
              transcriptSave.fehlgeschlagen > 0
                ? <button type="button" className="cc-pill warn" title="Äußerungen liegen sicher in diesem Browser und werden erneut übertragen." onClick={onRetryTranscripts}>Transkript: {transcriptSave.fehlgeschlagen} fehlgeschlagen – erneut senden</button>
                : <span className={`cc-pill ${transcriptSave.ausstehend ? "attn" : "ok"}`} title="Jede erkannte Äußerung wird sofort ins Gesprächsarchiv geschrieben (nur Text, kein Audio).">
                    Transkript: {transcriptSave.gespeichert} gespeichert{transcriptSave.ausstehend ? ` · ${transcriptSave.ausstehend} ausstehend` : ""}</span>
            )}
            {openApprovals.length > 0 && section !== "freigaben" && section !== "uebersicht" && (
              <button type="button" className="cc-pill attn" onClick={() => go("freigaben")}>{openApprovals.length} {openApprovals.length === 1 ? "Freigabe wartet" : "Freigaben warten"}</button>
            )}
            {section !== "gespraech" && canEdit && (
              <button type="button" className="cc-pill cc-kbd-hint" onClick={() => { go("gespraech"); setFocusComposer((n) => n + 1); }}
                aria-label="Command Center öffnen und schreiben">Jarvis fragen <kbd>⌘K</kbd></button>
            )}
          </div>
        </header>
        {offline && <div className="cc-error" role="status">{offline} Angezeigt wird der letzte bekannte Stand.</div>}
        {notice && (
          <div className="cc-notice" role="status">{notice} <button type="button" className="cc-link" onClick={() => setNotice(null)}>OK</button></div>
        )}

        <div className={`cc-work ${section === "welt" || section === "gespraech" ? "flush" : ""}`} key={section}>
          {section === "uebersicht" && (
            <TodayView
              dateLabel={new Date().toLocaleDateString("de-DE", { timeZone: tz, weekday: "long", day: "numeric", month: "long", year: "numeric" })}
              queue={queue} ready={ready} unchecked={unchecked} suggestions={suggestions}
              agents={agentData} agentError={agentError} calendar={calendar} nextEvent={nextEvent}
              canAct={canEdit} connected={connected} onAsk={ask} onOpen={openItem} onOpenJob={openJob} onOpenEvent={openEvent}
              onTalk={() => go("gespraech")} />
          )}
          {section === "auftraege" && (
            <div style={{ display: "grid", gridTemplateColumns: selected ? "minmax(0,1fr) 300px" : "1fr", gap: 16 }}>
              <KanbanPanel columns={columns} orders={board.orders} pending={board.pending} query={search} canEdit={canEdit}
                selectedId={selected?.order.id ?? null}
                onSelect={(order) => setSelected({ kind: "order", order })} onMove={(o, s, p) => void move(o, s, p)}
                onCreate={create} error={board.error} />
              {selected && (
                <aside className="cc-detail" style={{ alignSelf: "start" }} aria-label="Auftragsdetails">
                  <h3>{selected.order.titel}</h3>
                  <dl>
                    <dt>Status</dt><dd>{columns.find((c) => c.id === selected.order.status)?.titel}</dd>
                    <dt>Kunde</dt><dd>{selected.order.kunde || "–"}</dd>
                    <dt>Projekt</dt><dd>{selected.order.projekt || "–"}</dd>
                    <dt>Priorität</dt><dd>{selected.order.prioritaet}</dd>
                    <dt>Frist</dt><dd>{selected.order.frist || "–"}</dd>
                    <dt>Verknüpft</dt><dd>{selected.order.jobs.length} Jobs · {selected.order.runs.length} Läufe</dd>
                  </dl>
                  {selected.order.notiz && <p style={{ margin: 0 }}>{selected.order.notiz}</p>}
                  {selected.order.checkliste.length > 0 && (
                    <ul className="cc-list">{selected.order.checkliste.map((c, i) => <li key={i}>{c.erledigt ? "☑" : "☐"} {c.text}</li>)}</ul>
                  )}
                  <div className="cc-actions">
                    {canEdit && <button type="button" className="cc-btn small accent" onClick={() => ask(`Wie gehe ich den Auftrag "${selected.order.titel}" am besten an? Nächster Schritt?`)}>Nächsten Schritt fragen</button>}
                    <button type="button" className="cc-btn small" onClick={() => setSelected(null)}>Schließen</button>
                  </div>
                </aside>
              )}
            </div>
          )}
          {section === "kalender" && (
            <>
              <div className="cc-page-head">
                <div className="cc-tabs" role="group" aria-label="Kalenderansicht">
                  <button type="button" aria-pressed={calTab === "monat"} onClick={() => setCalTab("monat")}>Kalender</button>
                  <button type="button" aria-pressed={calTab === "liste"} onClick={() => setCalTab("liste")}>Agenda</button>
                </div>
              </div>
              {calTab === "monat" ? (
                <Suspense fallback={<p className="cc-empty">Kalender lädt …</p>}>
                  <CalendarPanel data={calendar}
                    onRange={(von, bis) => { if (range?.von === von && range?.bis === bis) return; setRange({ von, bis }); }}
                    onSelect={openEvent} />
                </Suspense>
              ) : (
                <AgendaPanel data={calendar} query={search} canAsk={canEdit} onAsk={ask} onSelect={openEvent} />
              )}
            </>
          )}
          {section === "termin" && (event
            ? <EventDetail event={event} data={calendar} canAsk={canEdit} onAsk={ask} onBack={() => go("kalender")} />
            : <p className="cc-empty">Kein Termin ausgewählt. <button type="button" className="cc-link" onClick={() => go("kalender")}>Zum Kalender</button></p>)}
          {section === "anrufe" && (
            <>
              <div className="cc-page-head"><div><h2>Vertrieb</h2><p>Vom Lead bis zur nächsten Umsatzaktion. Zahlen nur aus erfassten Gesprächen; ins CRM geht nichts ohne deine Freigabe.</p></div>
                <button type="button" className="cc-link" onClick={() => void loadCalls()}>Aktualisieren</button></div>
              <CallsPanel data={calls} error={callsError} canAsk={canEdit} onAsk={ask} days={callDays} onDays={(d) => { setCallDays(d); void loadCalls(d); }} onStartSkill={startSkill} />
            </>
          )}
          {section === "content" && (
            <>
              <div className="cc-page-head"><div><h2>Content</h2><p>Von der Idee bis zur Auswertung. Nichts wird automatisch veröffentlicht.</p></div>
                <button type="button" className="cc-link" onClick={() => void loadContent()}>Aktualisieren</button></div>
              <ContentPanel data={contentData} error={contentError} canEdit={canEdit} onMove={moveContent} onAsk={ask} onStartSkill={startSkill} />
            </>
          )}
          {section === "agenten" && (
            <>
              <div className="cc-page-head"><div><h2>Agenten-Center</h2><p>Echte Jobs mit Schritten, Zeiten und Ergebnis. Aktiv ist nur, wofür wirklich ein Job läuft.</p></div></div>
              <AgentCenter data={agentData} error={agentError} canAct={canEdit} focusJob={focusJob} busyId={capBusy}
                onCancel={(id) => void cancelJob(id)} onRetry={(id) => void capabilityAction(id, "wiederholen")}
                onApprovals={() => go("freigaben")} onOpenOrder={openOrder} onStartRole={startRole} onRefresh={() => void loadWork()} />
            </>
          )}
          {section === "automatisierungen" && (
            <>
              <div className="cc-page-head"><div><h2>Skills und Automationen</h2>
                <p>Vier verschiedene Dinge: Fähigkeiten (ausführbar), Skills (Anleitungen, die als Job laufen), geplante Abläufe und n8n-Workflows.</p></div></div>
              <SkillPlatform platform={platform} error={capError} canEdit={canEdit} busy={capBusy}
                onAction={(id, action) => void capabilityAction(id, action)} onOpenOrder={openOrder}
                onStart={startCapability} onToggleSkill={(id, active) => void toggleSkill(id, active)} onOpenApprovals={() => go("freigaben")} />
              <div className="cc-page-head ac-roles-head"><div><h2>Geplante Abläufe und Workflows</h2></div></div>
              <AutomationsPanel data={automations} error={automationError} />
            </>
          )}
          {section === "gedaechtnis" && (
            <>
              <div className="cc-page-head"><div><h2>Gedächtnis</h2><p>Dauerhaftes Wissen und Gesprächsarchiv – ansehen, suchen, korrigieren, bestätigen, exportieren, löschen.</p></div></div>
              <MemoryCenter canEdit={canEdit} />
            </>
          )}
          {section === "verbindungen" && (
            <>
              <div className="cc-page-head"><div><h2>Verbindungen und Einrichtung</h2><p>Was wirklich verbunden ist, was abgelaufen oder unvollständig ist – und was du dafür tun musst.</p></div></div>
              <ConnectionsPanel data={connections} error={connError} loading={connBusy} onRefresh={() => void loadConnections(true)} />
            </>
          )}
          {section === "welt" && (
            <Suspense fallback={<p className="cc-empty" style={{ padding: 24 }}>Karte lädt …</p>}>
              <MapboxWorld query={worldQuery} webcams={webcams} />
            </Suspense>
          )}
          {section === "gespraech" && (
            <CommandCenter telemetry={telemetry} audio={audio} role={role} connected={connected} transcripts={transcripts}
              micOn={micOn} onMicChange={onMicChange} muted={muted} onMutedChange={onMutedChange} onAsk={onAsk} onStop={onStop} onLog={onLog}
              automations={automations} approvals={approvals} onOpenApprovals={() => go("freigaben")}
              settings={settings} onSettings={setSettings} voiceMode={voiceMode} onStartRealtime={onStartRealtime}
              onStopRealtime={onStopRealtime} getRealtime={getRealtime} prefs={voicePrefs} onPrefs={onVoicePrefs} onReset={onReset}
              platform={platform} jobs={agentData?.jobs ?? null} jobsError={agentError} busyJob={capBusy}
              onOpenJob={openJob} onCancelJob={(id) => void cancelJob(id)} focusSignal={focusComposer} room={room} />
          )}
          {section === "freigaben" && <ApprovalsPanel items={approvals} canDecide={canEdit} onDecide={decide} query={search} />}
        </div>
      </div>
      <nav className="cc-tabbar" aria-label="Schnellnavigation">
        {TABS.map((t) => (
          <button key={t.id} type="button" className={isCurrent(t.id) ? "active" : ""} aria-current={isCurrent(t.id) ? "page" : undefined} onClick={() => go(t.id)}>
            <Icon name={t.icon} /><span>{t.id === "gespraech" ? "Command" : t.label}</span>{badge(t.id)}
          </button>
        ))}
        <button type="button" aria-expanded={navOpen} onClick={() => setNavOpen(true)}><Icon name="menu" /><span>Menü</span></button>
      </nav>
      {camera && <LiveCamera camera={camera} onClose={() => setCamera(null)} />}
    </section>
  );
}
