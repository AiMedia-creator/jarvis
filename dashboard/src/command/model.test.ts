import { describe, expect, it } from "vitest";
import type { CalendarEvent, Order } from "./api";
import { agendaDays, boardReducer, dayKey, initialBoard, phaseOf, positionFor, remaining } from "./model";
import { classifyUtterance } from "../view/intent";

const order = (id: string, status: Order["status"], position: number, version = 1): Order => ({
  id, version, titel: id, kunde: "", projekt: "", prioritaet: "normal", verantwortlich: "", frist: "", status, position,
  checkliste: [], abhaengigkeiten: [], quelle: "", notiz: "", jobs: [], runs: [], erstellt: 0, geaendert: 0,
});
const event = (id: string, extra: Partial<CalendarEvent>): CalendarEvent => ({
  id, kalender_id: "primary", kalender: "Omar", titel: id, ganztaegig: false, zeitzone: "Europe/Berlin", beginn: null,
  ende: null, datum_beginn: null, datum_ende: null, teilnehmer: [], ort: "", meeting_link: "", serie_id: null,
  ausnahme: false, status: "confirmed", frei: false, etag: null, link: "", gueltig: true, ...extra,
});

describe("Kanban-Zustand", () => {
  it("verschiebt optimistisch und rollt bei Fehler exakt zurück", () => {
    let s = boardReducer(initialBoard, { type: "load", orders: [order("A-1", "eingang", 1000)] });
    s = boardReducer(s, { type: "move", id: "A-1", status: "in_arbeit", position: 500 });
    expect(s.orders[0].status).toBe("in_arbeit");
    expect(s.pending["A-1"].status).toBe("eingang");
    // älterer Serverstand während der Bestätigung überschreibt die laufende Bewegung nicht
    s = boardReducer(s, { type: "load", orders: [order("A-1", "eingang", 1000)] });
    expect(s.orders[0].status).toBe("in_arbeit");
    s = boardReducer(s, { type: "rollback", id: "A-1", message: "Fehler" });
    expect(s.orders[0]).toMatchObject({ status: "eingang", position: 1000 });
    expect(s.error).toBe("Fehler");
    expect(s.pending).toEqual({});
  });
  it("übernimmt bei Konflikt den aktuellen Serverstand und bestätigt sonst die neue Version", () => {
    let s = boardReducer(initialBoard, { type: "load", orders: [order("A-1", "eingang", 1000)] });
    s = boardReducer(s, { type: "move", id: "A-1", status: "geplant", position: 1 });
    s = boardReducer(s, { type: "rollback", id: "A-1", message: "Konflikt", current: order("A-1", "erledigt", 9, 5) });
    expect(s.orders[0]).toMatchObject({ status: "erledigt", version: 5 });
    s = boardReducer(s, { type: "move", id: "A-1", status: "geplant", position: 1 });
    s = boardReducer(s, { type: "confirm", order: order("A-1", "geplant", 1, 6) });
    expect(s.orders[0].version).toBe(6);
  });
  it("berechnet Positionen zwischen Nachbarn", () => {
    const list = [order("a", "geplant", 1000), order("b", "geplant", 2000), order("x", "eingang", 1)];
    expect(positionFor(list, "geplant", "b", "x")).toBe(1500);
    expect(positionFor(list, "geplant", null, "x")).toBe(3000);
    expect(positionFor(list, "geplant", "a", "x")).toBe(500);
    expect(positionFor(list, "erledigt", null, "x")).toBe(1000);
  });
});

describe("Agenda", () => {
  const now = new Date("2026-10-19T08:00:00+02:00");
  it("gruppiert nach Kalendertag in der Zielzeitzone: Heute, Morgen, Datum", () => {
    const days = agendaDays([
      event("spaet", { beginn: "2026-10-19T23:30:00Z", ende: "2026-10-20T00:30:00Z" }), // Berlin: 20.10. 01:30
      event("heute", { beginn: "2026-10-19T14:00:00+02:00", ende: "2026-10-19T15:00:00+02:00" }),
      event("urlaub", { ganztaegig: true, datum_beginn: "2026-10-21", datum_ende: "2026-10-23" }), // Ende exklusiv
      event("abgesagt", { status: "cancelled", beginn: "2026-10-19T10:00:00+02:00", ende: "2026-10-19T11:00:00+02:00" }),
    ], "Europe/Berlin", now);
    expect(days.map((d) => [d.label, d.items.map((i) => i.id)])).toEqual([
      ["Heute", ["heute"]], ["Morgen", ["spaet"]], ["Mittwoch, 21. Oktober", ["urlaub"]], ["Donnerstag, 22. Oktober", ["urlaub"]],
    ]);
  });
  it("Zeitumstellung: Tag und Dauer bleiben korrekt", () => {
    expect(dayKey(new Date("2026-10-25T00:30:00Z"), "Europe/Berlin")).toBe("2026-10-25");
    expect(dayKey(new Date("2026-10-24T22:30:00Z"), "Europe/Berlin")).toBe("2026-10-25");
    expect(dayKey(new Date("2026-10-24T22:30:00Z"), "America/New_York")).toBe("2026-10-24");
  });
  it("markiert Vorbereitung, laufend und Follow-up", () => {
    expect(phaseOf(event("v", { beginn: "2026-10-19T14:00:00+02:00", ende: "2026-10-19T15:00:00+02:00" }), now)).toBe("vorbereitung");
    expect(phaseOf(event("l", { beginn: "2026-10-19T07:30:00+02:00", ende: "2026-10-19T09:00:00+02:00" }), now)).toBe("laeuft");
    expect(phaseOf(event("f", { beginn: "2026-10-18T09:00:00+02:00", ende: "2026-10-18T10:00:00+02:00" }), now)).toBe("followup");
    expect(phaseOf(event("g", { ganztaegig: true, datum_beginn: "2026-10-19", datum_ende: "2026-10-20" }), now)).toBeNull();
    expect(remaining(1000, 940_000)).toBe("noch 1 Min.");
    expect(remaining(1000, 1_000_000)).toBe("abgelaufen");
  });
});

describe("Sprachbefehle", () => {
  it("öffnet die Kommandozentrale nur auf ausdrücklichen Befehl, mit Bereich", () => {
    expect(classifyUtterance("Zeig mir meinen Kalender")).toMatchObject({ kind: "open", view: "command", query: "kalender" });
    expect(classifyUtterance("Öffne die Aufträge")).toMatchObject({ kind: "open", view: "command", query: "auftraege" });
    expect(classifyUtterance("Öffne die Kommandozentrale")).toMatchObject({ kind: "open", view: "command" });
    expect(classifyUtterance("Was steht heute im Kalender?")).toEqual({ kind: "none" });
    expect(classifyUtterance("Zeig mir nicht den Kalender")).toEqual({ kind: "none" });
  });
});
