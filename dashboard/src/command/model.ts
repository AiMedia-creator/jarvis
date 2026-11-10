/** Reine Logik der Kommandozentrale (ohne Seiteneffekte, gut testbar). */
import type { CalendarEvent, Order, OrderStatus } from "./api";

// ---------- Kanban: optimistisches Verschieben mit Rückweg ----------

export type BoardState = { orders: Order[]; pending: Record<string, Order>; error: string | null };
export type BoardAction =
  | { type: "load"; orders: Order[] }
  | { type: "move"; id: string; status: OrderStatus; position: number }
  | { type: "confirm"; order: Order }
  | { type: "rollback"; id: string; message: string; current?: Order }
  | { type: "add"; order: Order }
  | { type: "clear" };

export const initialBoard: BoardState = { orders: [], pending: {}, error: null };

export function boardReducer(state: BoardState, action: BoardAction): BoardState {
  switch (action.type) {
    case "load":
      // Laufende (noch unbestätigte) Verschiebungen nicht durch einen älteren Serverstand überschreiben.
      return {
        ...state,
        orders: action.orders.map((o) => (state.pending[o.id] ? state.orders.find((x) => x.id === o.id) ?? o : o)),
      };
    case "move": {
      const before = state.orders.find((o) => o.id === action.id);
      if (!before || state.pending[action.id]) return state;
      return {
        ...state,
        error: null,
        pending: { ...state.pending, [action.id]: before },
        orders: state.orders.map((o) =>
          o.id === action.id ? { ...o, status: action.status, position: action.position } : o,
        ),
      };
    }
    case "confirm": {
      const { [action.order.id]: _, ...rest } = state.pending;
      return { ...state, pending: rest, orders: state.orders.map((o) => (o.id === action.order.id ? action.order : o)) };
    }
    case "rollback": {
      const original = action.current ?? state.pending[action.id];
      const { [action.id]: _, ...rest } = state.pending;
      return {
        ...state,
        pending: rest,
        error: action.message,
        orders: original ? state.orders.map((o) => (o.id === action.id ? original : o)) : state.orders,
      };
    }
    case "add":
      return { ...state, orders: [...state.orders, action.order] };
    case "clear":
      return { ...state, error: null };
  }
}

export function columnOrders(orders: Order[], status: OrderStatus): Order[] {
  return orders.filter((o) => o.status === status).sort((a, b) => a.position - b.position);
}

/** Position für das Einfügen vor `beforeId` (oder ans Ende): Mitte zwischen den Nachbarn. */
export function positionFor(orders: Order[], status: OrderStatus, beforeId: string | null, movingId: string): number {
  const list = columnOrders(orders, status).filter((o) => o.id !== movingId);
  if (!list.length) return 1000;
  const index = beforeId ? list.findIndex((o) => o.id === beforeId) : -1;
  if (index < 0) return list[list.length - 1].position + 1000;
  const prev = index === 0 ? list[0].position - 1000 : list[index - 1].position;
  return (prev + list[index].position) / 2;
}

export function matches(order: Order, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return [order.titel, order.kunde, order.projekt, order.notiz, order.verantwortlich].some((v) =>
    (v || "").toLowerCase().includes(q),
  );
}

// ---------- Agenda: Heute / Morgen / Datum ----------

export type AgendaItem = CalendarEvent & { phase: "vorbereitung" | "laeuft" | "followup" | null };
export type AgendaDay = { key: string; label: string; items: AgendaItem[] };

/** Kalendertag (JJJJ-MM-TT) eines Zeitpunkts in einer IANA-Zeitzone. */
export function dayKey(date: Date, timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" })
    .formatToParts(date);
  const get = (t: string) => parts.find((p) => p.type === t)?.value;
  return `${get("year")}-${get("month")}-${get("day")}`;
}

function addDays(key: string, days: number): string {
  const d = new Date(`${key}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export function phaseOf(event: CalendarEvent, now: Date): AgendaItem["phase"] {
  if (event.ganztaegig || !event.beginn || !event.ende || !event.gueltig) return null;
  const start = new Date(event.beginn).getTime();
  const end = new Date(event.ende).getTime();
  const t = now.getTime();
  if (t >= start && t < end) return "laeuft";
  if (start > t && start - t <= 24 * 3600_000) return "vorbereitung";
  if (end <= t && t - end <= 48 * 3600_000) return "followup";
  return null;
}

export function agendaDays(events: CalendarEvent[], timeZone: string, now = new Date()): AgendaDay[] {
  const today = dayKey(now, timeZone);
  const tomorrow = addDays(today, 1);
  const byDay = new Map<string, AgendaItem[]>();
  for (const event of events) {
    if (event.status === "cancelled") continue;
    const keys: string[] = [];
    if (event.ganztaegig && event.datum_beginn) {
      // Google: Ende exklusiv; mehrtägige Termine an jedem Tag zeigen (begrenzt).
      const last = event.datum_ende && event.datum_ende > event.datum_beginn ? addDays(event.datum_ende, -1) : event.datum_beginn;
      for (let k = event.datum_beginn, n = 0; k <= last && n < 31; k = addDays(k, 1), n++) keys.push(k);
    } else if (event.beginn) {
      keys.push(dayKey(new Date(event.beginn), timeZone));
    }
    for (const key of keys) {
      const list = byDay.get(key) ?? [];
      list.push({ ...event, phase: phaseOf(event, now) });
      byDay.set(key, list);
    }
  }
  const fmt = new Intl.DateTimeFormat("de-DE", { timeZone: "UTC", weekday: "long", day: "numeric", month: "long" });
  return [...byDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, items]) => ({
      key,
      label: key === today ? "Heute" : key === tomorrow ? "Morgen" : fmt.format(new Date(`${key}T12:00:00Z`)),
      items: items.sort((a, b) => Number(b.ganztaegig) - Number(a.ganztaegig) || (a.beginn ?? "").localeCompare(b.beginn ?? "")),
    }));
}

export function timeRange(event: CalendarEvent, timeZone: string): string {
  if (event.ganztaegig) return "ganztägig";
  if (!event.beginn || !event.ende) return "Zeit unbekannt";
  const fmt = new Intl.DateTimeFormat("de-DE", { timeZone, hour: "2-digit", minute: "2-digit" });
  return `${fmt.format(new Date(event.beginn))}–${fmt.format(new Date(event.ende))}`;
}

export function remaining(until: number, now = Date.now()): string {
  const s = Math.round(until - now / 1000);
  if (s <= 0) return "abgelaufen";
  const m = Math.floor(s / 60);
  return m >= 1 ? `noch ${m} Min.` : `noch ${s} s`;
}
