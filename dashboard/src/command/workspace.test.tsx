// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import type { CalendarData, CalendarEvent } from "./api";
import { EventDetail } from "./EventDetail";
import { mapboxToken, LAYERS } from "../world/MapboxWorld";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const event: CalendarEvent = {
  id: "e1", kalender_id: "k", kalender: "Arbeit", titel: "Kundentermin Müller", ganztaegig: false, zeitzone: "Europe/Berlin",
  beginn: "2026-11-12T10:00:00+01:00", ende: "2026-11-12T11:00:00+01:00", datum_beginn: null, datum_ende: null,
  teilnehmer: [{ email: "a@mueller.example", name: "Anna Müller", antwort: "accepted", ich: false }],
  ort: "Büro Berlin", meeting_link: "https://meet.example/abc", serie_id: null, ausnahme: false, status: "confirmed",
  frei: false, etag: null, link: "", gueltig: true,
};
const data: CalendarData = { status: "ok", zeitzone: "Europe/Berlin", termine: [event], konflikte: [] };

describe("Termin-Detail", () => {
  it("zeigt echte Felder und fragt Jarvis erst auf Klick", async () => {
    const root = document.createElement("div");
    document.body.append(root);
    const onAsk = vi.fn();
    await act(async () => createRoot(root).render(<EventDetail event={event} data={data} canAsk onAsk={onAsk} onBack={() => undefined} />));
    expect(root.textContent).toContain("Kundentermin Müller");
    expect(root.textContent).toContain("10:00–11:00");
    expect(root.textContent).toContain("Anna Müller");
    expect(root.textContent).toContain("zugesagt");
    expect(onAsk).not.toHaveBeenCalled();
    const prep = [...root.querySelectorAll("button")].find((b) => b.textContent === "Vorbereitung erstellen")!;
    await act(async () => prep.click());
    expect(onAsk).toHaveBeenCalledWith(expect.stringContaining("Kundentermin Müller"));
  });
  it("Beobachter sehen keine Jarvis-Aktionen", async () => {
    const root = document.createElement("div");
    await act(async () => createRoot(root).render(<EventDetail event={event} data={data} canAsk={false} onAsk={() => undefined} onBack={() => undefined} />));
    expect(root.textContent).not.toContain("Vorbereitung erstellen");
  });
});

describe("Mapbox-Token", () => {
  it("akzeptiert nur öffentliche pk.-Tokens", () => {
    expect(mapboxToken(undefined)).toBeNull();
    expect(mapboxToken("")).toBeNull();
    expect(mapboxToken("pk.DEIN_MAPBOX_TOKEN")).toBeNull(); // Platzhalter aus .env.example
    expect(mapboxToken("sk.eyJ1IjoiYWJjIiwiYSI6ImRlZiJ9.geheim123456")).toBeNull();
    expect(mapboxToken(" pk.eyJ1IjoiYWJjIiwiYSI6ImRlZiJ9.abcdefghijk ")).toBe("pk.eyJ1IjoiYWJjIiwiYSI6ImRlZiJ9.abcdefghijk");
  });
  it("bietet Satellit, Satellit + Straßen und Straßen an", () => {
    expect(LAYERS.map((l) => l.id)).toEqual(expect.arrayContaining(["satellite-streets", "satellite", "streets"]));
  });
});
