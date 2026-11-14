import { describe, expect, it } from "vitest";
import type { Order } from "./api";
import { buildQueue, buildSuggestions, type QueueInput } from "./today";

const NOW = Date.parse("2026-11-11T10:00:00Z");
const order = (o: Partial<Order>): Order => ({ id: "A-1", version: 1, titel: "Auftrag", kunde: "", projekt: "", prioritaet: "normal", verantwortlich: "",
  frist: "", status: "eingang", position: 0, checkliste: [], abhaengigkeiten: [], quelle: "", notiz: "", jobs: [], runs: [], erstellt: 0, geaendert: 0, ...o });
const empty: QueueInput = { approvals: [], orders: [], today: "2026-11-11", calls: null, content: null, calendar: null, automations: null, connections: null, agents: null, now: NOW };

describe("Heute: Braucht dich", () => {
  it("ist leer, wenn nichts wartet – und erfindet nichts ohne Daten", () => {
    expect(buildQueue(empty)).toEqual([]);
    expect(buildQueue({ ...empty, orders: [order({ frist: "2026-12-01" }), order({ id: "A-2", frist: "2026-11-01", status: "erledigt" })] })).toEqual([]);
  });

  it("ordnet fest: Freigabe, Fehlschlag, überfällig, heute, Follow-up, Review, Pflege", () => {
    const queue = buildQueue({
      ...empty,
      approvals: [{ id: "f1", werkzeug: "mail_senden", status: "WAITING_FOR_APPROVAL", status_text: "", ergebnis: "", erstellt: 0, laeuft_ab: NOW / 1000 + 600,
        job: null, graph: null, vorschau: { titel: "E-Mail an Weber", ziel: "Gmail", zeilen: [] } },
        { id: "f0", werkzeug: "x", status: "APPROVED", status_text: "", ergebnis: "", erstellt: 0, laeuft_ab: 0, job: null, graph: null, vorschau: { titel: "alt", ziel: "", zeilen: [] } }],
      orders: [order({ id: "A-3", titel: "Heute", frist: "2026-11-11" }), order({ id: "A-2", titel: "Normal überfällig", frist: "2026-11-01" }),
        order({ id: "A-1", titel: "Kritisch überfällig", frist: "2026-11-09", prioritaet: "kritisch" })],
      agents: { stand: 0, hinweis: "", agenten: [], rollen: [], zahlen: {}, jobs: [
        { id: "j1", titel: "Recherche", status: "FAILED", ende: NOW / 1000 - 3600, start: 0, letzte_aktivitaet: 0, fehler: "Zeitlimit", agent: { id: "a", name: "Rechercheur", art: "" },
          unterbrochen_durch_neustart: false, wiederholbar: true },
        { id: "j0", titel: "Uralt", status: "FAILED", ende: NOW / 1000 - 3 * 86400, start: 0, letzte_aktivitaet: 0, fehler: "x", agent: { id: "a", name: "A", art: "" },
          unterbrochen_durch_neustart: false, wiederholbar: false },
      ] as any },
      calls: { projekte: [], ergebnisse: {}, kennzahlen: { crm_offen: 2 } as any, letzte_anrufe: [
        { id: "c1", firma: "Kanzlei Weber", lead_name: "", naechster_schritt: "Angebot nachfassen", frist: "2026-11-10" },
        { id: "c2", firma: "Später", lead_name: "", naechster_schritt: "Rückruf", frist: "2026-11-20" },
      ] as any },
      content: { spalten: [], plattformen: [], quelle: "", eintraege: [{ id: "k1", status: "review", titel: "Reel Steuertipps" }, { id: "k2", status: "idee", titel: "Idee" }] as any },
      calendar: { status: "ok", termine: [], konflikte: [{ a: "e1", b: "e2", titel: ["Call", "Zahnarzt"], ab: "", bis: "" }] },
      connections: { stand: "", braucht_dich: 1, verbindungen: [
        { id: "google", name: "Google", zweck: "", zustand: "abgelaufen", pruefung: "live", details: [], schritte: [], hinweis: "Neu anmelden", dienste: {} },
        { id: "n8n", name: "n8n", zweck: "", zustand: "nicht_eingerichtet", pruefung: "konfiguration", details: [], schritte: [], hinweis: "", dienste: {} }] },
    });
    expect(queue.map((q) => q.title)).toEqual([
      "E-Mail an Weber", "Recherche", "Kritisch überfällig", "Normal überfällig", "Heute", "Angebot nachfassen", "Reel Steuertipps",
      "„Call“ und „Zahnarzt“ überschneiden sich", "2 Anrufe ohne CRM-Eintrag", "Google",
    ]);
    expect(queue[0]).toMatchObject({ kind: "freigabe", target: "freigaben", tone: "attn" });
    expect(queue[0].reason).toContain("noch 10 Min.");
    expect(queue[1]).toMatchObject({ jobId: "j1", action: "Ansehen und wiederholen", tone: "danger" });
    expect(queue[2]).toMatchObject({ orderId: "A-1", target: "auftraege" });
    expect(new Set(queue.map((q) => q.id)).size).toBe(queue.length);
  });

  it("meldet einen nicht lesbaren Kalender, aber keinen Demo-Kalender", () => {
    expect(buildQueue({ ...empty, calendar: { status: "fehler", hinweis: "Token abgelaufen", termine: [], konflikte: [] } })[0]).toMatchObject({ title: "Kalender nicht verfügbar", reason: "Token abgelaufen" });
    expect(buildQueue({ ...empty, calendar: { status: "demo", termine: [], konflikte: [] } })).toEqual([]);
  });

  it("schlägt nur Sätze mit echtem Bezug vor", () => {
    expect(buildSuggestions({ orders: [], today: "2026-11-11", calls: null }).map((s) => s.label)).toEqual(["Tagesbriefing", "Auftrag anlegen"]);
    const full = buildSuggestions({ orders: [order({ titel: "Website Kanzlei", frist: "2026-11-01" })], today: "2026-11-11", calls: null, nextEvent: { titel: "Kundentermin", zeit: "heute 14:00" } });
    expect(full.map((s) => s.label)).toEqual(["Tagesbriefing", "Termin vorbereiten: Kundentermin", "Nächster Schritt: Website Kanzlei", "Auftrag anlegen"]);
    expect(full[1].text).toContain("Kundentermin");
  });
});

describe("Heute: Follow-ups aus dem CRM", () => {
  const call = (o: Record<string, unknown>) => ({ id: "C-1", projekt_id: "crm", erstellt: 0, lead_name: "Frau Beispiel", firma: "Alpha AG", rolle: "", telefon_status: "geschaeftlich",
    kontakt_quelle: "", ergebnis: "rueckruf", notiz: "", naechster_schritt: "Follow-up-Anruf", frist: "2026-11-10", crm_status: "nicht_noetig", dauer_s: null, auftrag_id: "", ...o });
  const calls = (list: ReturnType<typeof call>[]) => ({ projekte: [], letzte_anrufe: list, ergebnisse: {},
    kennzahlen: { zeitraum_tage: 7, gespraeche: list.length, nach_ergebnis: {}, termine: 0, termine_pro_100: null, crm_offen: 0, followups_faellig: 0,
      mit_naechstem_schritt: 0, mittlere_dauer_s: null, quelle: "", stand: "" } }) as unknown as QueueInput["calls"];
  const lead = (o: Record<string, unknown>) => ({ id: "L-1", status: "follow_up", firma: "Alpha AG", name: "Frau Beispiel", naechster_schritt: "Angebot nachfassen", followup_am: "2026-11-10", ...o }) as any;

  it("führt den aktuellen Lead-Stand und zeigt erledigte Follow-ups nicht ewig als fällig", () => {
    // Der alte Anruf (mit lead_id) hätte früher dauerhaft „überfällig“ gemeldet; der Lead ist inzwischen gebucht.
    const erledigt = buildQueue({ ...empty, calls: calls([call({ lead_id: "L-1" })]), leads: [lead({ status: "meeting_gebucht", followup_am: "" })] });
    expect(erledigt).toEqual([]);
    const faellig = buildQueue({ ...empty, calls: calls([call({ lead_id: "L-1" })]), leads: [lead({}), lead({ id: "L-2", followup_am: "2026-12-01" })] });
    expect(faellig).toHaveLength(1);
    expect(faellig[0]).toMatchObject({ id: "lead-L-1", kind: "followup", tone: "danger", title: "Angebot nachfassen", target: "anrufe", leadId: "L-1", action: "Lead öffnen" });
    // Diktierte Anrufe ohne Lead bleiben wie bisher sichtbar
    expect(buildQueue({ ...empty, calls: calls([call({})]) })[0]).toMatchObject({ id: "followup-C-1", action: "Im CRM öffnen" });
    expect(buildSuggestions({ orders: [], today: "2026-11-11", calls: calls([call({ lead_id: "L-1" })]) }).some((s) => s.label.startsWith("Follow-up"))).toBe(false);
  });
});
