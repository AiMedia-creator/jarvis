/** „Heute“: eine einzige, fest priorisierte Liste dessen, was den Nutzer braucht – nur aus vorhandenen Daten. */
import type { AgentCenterData, Approval, Automations, CalendarData, CallOverview, Connections, ContentOverview, Order } from "./api";
import { remaining } from "./model";

export type QueueKind = "freigabe" | "job" | "auftrag" | "followup" | "crm" | "content" | "kalender" | "ablauf" | "verbindung";
export type QueueTarget = "freigaben" | "agenten" | "auftraege" | "anrufe" | "content" | "kalender" | "automatisierungen" | "verbindungen";
export type QueueItem = {
  id: string;
  kind: QueueKind;
  /** danger = etwas ist schiefgegangen oder überfällig, attn = wartet auf dich, warn = Pflege. */
  tone: "danger" | "attn" | "warn";
  label: string;
  title: string;
  reason: string;
  action: string;
  target: QueueTarget;
  orderId?: string;
  jobId?: string;
};
export type QueueInput = {
  approvals: Approval[];
  orders: Order[];
  today: string;
  calls: CallOverview | null;
  content: ContentOverview | null;
  calendar: CalendarData | null;
  automations: Automations | null;
  connections: Connections | null;
  agents: AgentCenterData | null;
  /** Millisekunden seit Epoche. */
  now?: number;
};

const PRIO = ["kritisch", "hoch", "normal", "niedrig"];
export const byPriority = (a: Order, b: Order) =>
  PRIO.indexOf(a.prioritaet) - PRIO.indexOf(b.prioritaet) || (a.frist || "9").localeCompare(b.frist || "9");
const DAY_S = 24 * 3600;
const CONNECTION_LABEL: Record<string, string> = { abgelaufen: "Zugang abgelaufen", unvollstaendig: "Einrichtung unvollständig", fehler: "Verbindungsfehler" };

/**
 * Reihenfolge (fest, nachvollziehbar): erst was Agenten blockiert, dann was schiefging, dann Fristen, dann Pflege.
 * Keine Punktzahlen, keine Schätzungen – jeder Eintrag entspricht genau einem Datensatz.
 */
export function buildQueue(input: QueueInput): QueueItem[] {
  const now = input.now ?? Date.now();
  const items: QueueItem[] = [];

  for (const a of input.approvals.filter((x) => x.status === "WAITING_FOR_APPROVAL"))
    items.push({
      id: `freigabe-${a.id}`, kind: "freigabe", tone: "attn", label: "Freigabe",
      title: a.vorschau?.titel || a.werkzeug || "Freigabe entscheiden",
      reason: `Ein Agent wartet auf deine Entscheidung${a.vorschau?.ziel ? ` · Ziel: ${a.vorschau.ziel}` : ""} · ${remaining(a.laeuft_ab, now)}`,
      action: "Entscheiden", target: "freigaben",
    });

  const failed = (input.agents?.jobs ?? [])
    .filter((j) => j.status === "FAILED" && (j.ende ?? j.letzte_aktivitaet ?? j.start) >= now / 1000 - DAY_S)
    .slice(0, 3);
  for (const j of failed)
    items.push({
      id: `job-${j.id}`, kind: "job", tone: "danger", label: j.unterbrochen_durch_neustart ? "Unterbrochen" : "Fehlgeschlagen",
      title: j.titel,
      reason: j.unterbrochen_durch_neustart ? `${j.agent.name} wurde durch einen Neustart unterbrochen.` : `${j.agent.name}: ${j.fehler || "ohne Fehlermeldung beendet"}`,
      action: j.wiederholbar ? "Ansehen und wiederholen" : "Ansehen", target: "agenten", jobId: j.id,
    });

  const open = input.orders.filter((o) => o.status !== "erledigt");
  for (const o of open.filter((x) => x.frist && x.frist < input.today).sort(byPriority))
    items.push({
      id: `auftrag-${o.id}`, kind: "auftrag", tone: "danger", label: "Überfällig",
      title: o.titel, reason: `Frist war ${o.frist} · Priorität ${o.prioritaet}${o.kunde ? ` · ${o.kunde}` : ""}`,
      action: "Auftrag öffnen", target: "auftraege", orderId: o.id,
    });
  for (const o of open.filter((x) => x.frist === input.today).sort(byPriority))
    items.push({
      id: `auftrag-${o.id}`, kind: "auftrag", tone: "attn", label: "Heute fällig",
      title: o.titel, reason: `Priorität ${o.prioritaet}${o.kunde ? ` · ${o.kunde}` : ""}`,
      action: "Auftrag öffnen", target: "auftraege", orderId: o.id,
    });

  const due = (input.calls?.letzte_anrufe ?? [])
    .filter((c) => c.naechster_schritt && c.frist && c.frist <= input.today)
    .sort((a, b) => a.frist.localeCompare(b.frist));
  for (const c of due)
    items.push({
      id: `followup-${c.id}`, kind: "followup", tone: c.frist < input.today ? "danger" : "attn", label: "Follow-up",
      title: c.naechster_schritt,
      reason: `${c.firma || c.lead_name || "Kontakt ohne Namen"} · ${c.frist < input.today ? `überfällig seit ${c.frist}` : "heute fällig"}`,
      action: "Im Vertrieb öffnen", target: "anrufe",
    });

  for (const e of (input.content?.eintraege ?? []).filter((x) => x.status === "review"))
    items.push({
      id: `content-${e.id}`, kind: "content", tone: "attn", label: "Review",
      title: e.titel || e.thema || "Content-Eintrag", reason: "Wartet auf dein Review – ohne dich geht es nicht weiter.",
      action: "Prüfen", target: "content",
    });

  const cal = input.calendar;
  for (const k of (cal?.konflikte ?? []).slice(0, 2))
    items.push({
      id: `konflikt-${k.a}-${k.b}`, kind: "kalender", tone: "warn", label: "Terminkonflikt",
      title: `„${k.titel[0]}“ und „${k.titel[1]}“ überschneiden sich`, reason: "Beide Termine belegen dieselbe Zeit.",
      action: "Kalender öffnen", target: "kalender",
    });
  if (cal && cal.status !== "ok" && cal.status !== "demo")
    items.push({
      id: "kalender-status", kind: "kalender", tone: "warn", label: "Kalender",
      title: "Kalender nicht verfügbar", reason: cal.hinweis || "Termine können gerade nicht gelesen werden.",
      action: "Details", target: "kalender",
    });

  const crm = input.calls?.kennzahlen.crm_offen ?? 0;
  if (crm > 0)
    items.push({
      id: "crm-offen", kind: "crm", tone: "warn", label: "CRM",
      title: `${crm} ${crm === 1 ? "Anruf" : "Anrufe"} ohne CRM-Eintrag`, reason: "Ins CRM geht nichts ohne deine Freigabe.",
      action: "Im Vertrieb öffnen", target: "anrufe",
    });

  for (const w of (input.automations?.ablaeufe ?? []).filter((x) => x.aktiv && x.letzter_lauf && ["FAILED", "UNCLEAR"].includes(x.letzter_lauf.status)))
    items.push({
      id: `ablauf-${w.id}`, kind: "ablauf", tone: "warn", label: "Ablauf",
      title: `Ablauf „${w.name}“`,
      reason: `Letzter Lauf ${w.letzter_lauf!.status === "FAILED" ? "fehlgeschlagen" : "unklar"}${w.letzter_lauf!.fehler ? `: ${w.letzter_lauf!.fehler}` : "."}`,
      action: "Ansehen", target: "automatisierungen",
    });

  for (const c of (input.connections?.verbindungen ?? []).filter((x) => x.zustand in CONNECTION_LABEL))
    items.push({
      id: `verbindung-${c.id}`, kind: "verbindung", tone: "warn", label: CONNECTION_LABEL[c.zustand],
      title: c.name, reason: c.hinweis || c.zweck, action: "Einrichten", target: "verbindungen",
    });

  return items;
}

export type Suggestion = { label: string; text: string };
/** Vorschläge für die Befehlszeile: nur Sätze, die sich auf echte Daten beziehen; sie füllen das Feld, senden aber nichts. */
export function buildSuggestions(input: Pick<QueueInput, "orders" | "today" | "calls"> & { nextEvent?: { titel: string; zeit: string } | null }): Suggestion[] {
  const out: Suggestion[] = [{ label: "Tagesbriefing", text: "Was steht heute an? Gib mir die drei wichtigsten Punkte." }];
  if (input.nextEvent)
    out.push({ label: `Termin vorbereiten: ${input.nextEvent.titel}`, text: `Bereite mich auf den Termin "${input.nextEvent.titel}" (${input.nextEvent.zeit}) vor.` });
  const overdue = input.orders.filter((o) => o.status !== "erledigt" && o.frist && o.frist <= input.today).sort(byPriority)[0];
  if (overdue)
    out.push({ label: `Nächster Schritt: ${overdue.titel}`, text: `Wie gehe ich den Auftrag "${overdue.titel}" am besten an? Nächster Schritt?` });
  const call = (input.calls?.letzte_anrufe ?? []).find((c) => c.naechster_schritt && c.frist && c.frist <= input.today);
  if (call)
    out.push({ label: `Follow-up entwerfen: ${call.firma || call.lead_name}`, text: `Entwirf das Follow-up für ${call.firma || call.lead_name} als Vorlage: ${call.naechster_schritt}` });
  out.push({ label: "Auftrag anlegen", text: "Lege einen Auftrag an: " });
  return out.slice(0, 4);
}
