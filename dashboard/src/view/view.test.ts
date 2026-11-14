import { describe, expect, it } from "vitest";
import { initialView, viewReducer, type ViewAction, type ViewModel } from "./controller";
import { classifyUtterance, type SecurityName } from "./intent";

const DIR: SecurityName[] = [
  { symbol: "AAPL", name: "Apple Inc.", aliases: ["apple"] },
  { symbol: "TSLA", name: "Tesla Inc.", aliases: ["tesla"] },
  { symbol: "SAP", name: "SAP SE", aliases: ["sap"] },
];
const c = (text: string) => classifyUtterance(text, DIR);

describe("Absichtserkennung", () => {
  it("Smalltalk bleibt im Gespräch", () => {
    for (const text of ["Wie geht es dir?", "Erzähl mir einen Witz.", "Danke, Jarvis.", ""])
      expect(c(text)).toEqual({ kind: "none" });
  });

  it("Nachrichten öffnen die Welt, auch mit Ort", () => {
    expect(c("Was gibt es Neues?")).toMatchObject({ kind: "open", view: "news", explicit: false });
    expect(c("Zeig mir die Nachrichten")).toMatchObject({ view: "news", explicit: true });
    expect(c("Was ist heute in Paris los?")).toMatchObject({ view: "news", query: "Paris" });
    expect(c("Nachrichten aus Berlin bitte")).toMatchObject({ view: "news", query: "Berlin" });
  });

  it("Negation verhindert den Wechsel", () => {
    expect(c("Ich mag die Nachrichtenansicht nicht.")).toEqual({ kind: "none" });
    expect(c("Keine Nachrichten jetzt")).toEqual({ kind: "none" });
    expect(c("Nicht die Nachrichten, sondern zeig mir die Kameras")).toMatchObject({ view: "cameras" });
  });

  it("Aktien nur bei konkretem Wertpapier, Erklärungen ohne Chart", () => {
    expect(c("Wie hat sich Apple entwickelt?")).toMatchObject({ view: "market", query: "apple" });
    expect(c("Zeig mir den Tesla-Kurs")).toMatchObject({ view: "market", query: "tesla", explicit: true });
    expect(c("Wie steht der Kurs von NVDA?")).toMatchObject({ view: "market", query: "NVDA" });
    expect(c("Was ist eine Aktie?")).toEqual({ kind: "none" });
    expect(c("Ich habe heute einen Apple-Kuchen gebacken")).toEqual({ kind: "none" });
    expect(c("Zeig mir einen Börsenchart")).toMatchObject({ view: "market", explicit: true });
  });

  it("Kameras, Gedächtnis und System nur gezielt", () => {
    expect(c("Welche Kameras gibt es?")).toMatchObject({ view: "cameras" });
    expect(c("Was hast du über mich gespeichert?")).toMatchObject({ view: "memory" });
    expect(c("Merke dir, dass ich Kaffee mag und speicher das im Gedächtnis")).toEqual({ kind: "none" });
    expect(c("Zeig mir die Systemmetriken")).toMatchObject({ view: "ops", explicit: true });
    expect(c("Das System ist gut")).toEqual({ kind: "none" });
  });

  it("Rückkehr-Befehle", () => {
    for (const text of ["Zurück", "Zurück zum Gespräch", "Ich will nur mit dir sprechen", "Schließ die Ansicht", "Lass uns über etwas anderes reden"])
      expect(c(text)).toEqual({ kind: "back" });
    expect(c("Zurück zum Gespräch, nicht die Nachrichten")).toEqual({ kind: "back" });
  });
});

function run(actions: ViewAction[], from: ViewModel = initialView): ViewModel {
  return actions.reduce(viewReducer, from);
}
const news = c("Was gibt es Neues?");

describe("Ansichts-Controller", () => {
  it("startet mit der reinen Gesprächsansicht", () => {
    expect(initialView.view).toBe("conversation");
  });

  it("Frage im aktuellen Zug öffnet, Rückkehr schließt", () => {
    const s = run([{ type: "turn", stream: "a" }, { type: "utterance", stream: "a", intent: news }]);
    expect(s.view).toBe("news");
    expect(run([{ type: "utterance", stream: "a", intent: { kind: "back" } }], s).view).toBe("conversation");
    expect(run([{ type: "back" }], s).view).toBe("conversation");
  });

  it("Transkript vor TURN_START wird angenommen (Server sendet es zuerst)", () => {
    const s = run([{ type: "utterance", stream: "b", intent: news }, { type: "turn", stream: "b" }]);
    expect(s.view).toBe("news");
  });

  it("verspätete Ereignisse eines abgebrochenen oder älteren Zugs öffnen nichts", () => {
    const stopped = run([{ type: "turn", stream: "a" }, { type: "stopped", stream: "a" }, { type: "utterance", stream: "a", intent: news }]);
    expect(stopped.view).toBe("conversation");
    const tool = run([{ type: "turn", stream: "a" }, { type: "stopped", stream: null }, { type: "tool", stream: "a", name: "nachrichten" }]);
    expect(tool.view).toBe("conversation");
    const older = run([{ type: "turn", stream: "a" }, { type: "turn", stream: "b" }, { type: "utterance", stream: "a", intent: news }]);
    expect(older.view).toBe("conversation");
  });

  it("manuelle Ansicht hat Vorrang vor schwachen Hinweisen, nicht vor ausdrücklichen Befehlen", () => {
    const pinned = run([{ type: "manual", view: "cameras" }, { type: "turn", stream: "a" }]);
    expect(run([{ type: "utterance", stream: "a", intent: news }], pinned).view).toBe("cameras");
    expect(run([{ type: "tool", stream: "a", name: "nachrichten" }], pinned).view).toBe("cameras");
    expect(run([{ type: "utterance", stream: "a", intent: c("Zeig mir die Nachrichten") }], pinned).view).toBe("news");
  });

  it("Werkzeug-Hinweis wirkt nur aus der Gesprächsansicht", () => {
    const s = run([{ type: "turn", stream: "a" }, { type: "tool", stream: "a", name: "nachrichten" }]);
    expect(s).toMatchObject({ view: "news", strength: 1, origin: "tool" });
    expect(run([{ type: "tool", stream: "a", name: "mails_suchen" }]).view).toBe("conversation");
  });

  it("ein Ansichtswechsel erhöht nur die Revision, Wiederholungen nicht", () => {
    const s = run([{ type: "manual", view: "memory" }, { type: "manual", view: "memory" }]);
    expect(s.revision).toBe(1);
  });
});

import { findFocus, markersFrom, type NewsItem } from "../views/NewsView";

describe("Nachrichten-Markierungen", () => {
  const item = (titel: string, ort: NewsItem["ort"]): NewsItem => ({ quelle: "Q", titel, text: "", zeit: null, link: "", ort });
  it("nur echte Orte, gleiche Koordinaten zusammengefasst, Stadtname bevorzugt", () => {
    const markers = markersFrom([
      item("a", { name: "Russland (Moskau)", lat: 55.756, lon: 37.617, art: "land" }),
      item("b", { name: "Moskau", lat: 55.756, lon: 37.617, art: "stadt" }),
      item("c", null),
    ]);
    expect(markers).toHaveLength(1);
    expect(markers[0]).toMatchObject({ name: "Moskau", count: 2 });
    expect(findFocus(markers, "Moskau")?.name).toBe("Moskau");
    expect(findFocus(markers, "Paris")).toBeUndefined();
  });
});

describe("Ortsbefehl „Zeig Berlin“", () => {
  const c = (text: string) => classifyUtterance(text, [{ symbol: "TSLA", name: "Tesla", aliases: ["tesla"] }]);
  it("öffnet die Karte mit dem Ort", () => {
    expect(c("Zeig Berlin")).toMatchObject({ kind: "open", view: "world", explicit: true, query: "Berlin" });
    expect(c("Zeig mir bitte München.")).toMatchObject({ view: "world", query: "München" });
  });
  it("verdrängt weder Nachrichten noch Markt noch Kameras", () => {
    expect(c("Zeig Nachrichten aus Berlin")).toMatchObject({ view: "news", query: "Berlin" });
    expect(c("Zeig mir den Tesla-Kurs")).toMatchObject({ view: "market" });
    expect(c("Zeig die Kameras in Berlin")).toMatchObject({ view: "cameras" });
  });
  it("reagiert nicht auf Verneinung oder unbekannte Orte", () => {
    expect(c("Zeig nicht Berlin")).toEqual({ kind: "none" });
    expect(c("Zeig Entenhausen")).toEqual({ kind: "none" });
  });
});

describe("Ortsbefehl auf Ägyptisch", () => {
  it("öffnet die Karte für bekannte Städte", () => {
    expect(classifyUtterance("وريني برلين")).toMatchObject({ kind: "open", view: "world", explicit: true, query: "Berlin" });
    expect(classifyUtterance("افتح خريطة القاهرة؟")).toMatchObject({ view: "world", query: "Kairo" });
    expect(classifyUtterance("وريني الإسكندرية")).toMatchObject({ view: "world", query: "Alexandria" });
  });
  it("ignoriert Unbekanntes und normale Sätze", () => {
    expect(classifyUtterance("وريني الإيميلات")).toEqual({ kind: "none" });
    expect(classifyUtterance("إزيك يا جارفس")).toEqual({ kind: "none" });
  });
});
