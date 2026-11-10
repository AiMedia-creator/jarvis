import { describe, expect, it } from "vitest";
import type { StyleSpecification } from "maplibre-gl";
import { altitudeMeters, findPlace, formatAltitude, formatCoord, placeFromSearch } from "./places";
import { COLORS, buildStyle, satelliteOpacity } from "./style";
import { classifyUtterance } from "../view/intent";

describe("Orte und HUD", () => {
  it("findet gespeicherte Orte in natürlicher Sprache", () => {
    expect(findPlace("Zeig mir Wall Street")?.id).toBe("wall-street");
    expect(findPlace("London")?.id).toBe("london");
    expect(findPlace("die Frankfurter Börse")?.id).toBe("frankfurt");
    expect(findPlace("Paris")).toBeUndefined(); // -> Ortssuche
  });
  it("formatiert Koordinaten wie ein Instrument (Grad, Minuten, Sekunden, Halbkugel)", () => {
    expect(formatCoord(40.70614, "lat")).toBe("40°42′22.10″ N");
    expect(formatCoord(-74.00883, "lon")).toBe("074°00′31.79″ W");
    expect(formatCoord(-33.5, "lat").endsWith("S")).toBe(true);
  });
  it("Höhe sinkt mit dem Zoom und wird lesbar formatiert", () => {
    const world = altitudeMeters(1.5, 25), street = altitudeMeters(17, 40.7);
    expect(world).toBeGreaterThan(5_000_000);
    expect(street).toBeLessThan(2000);
    expect(formatAltitude(812)).toBe("812 m");
    expect(formatAltitude(1234)).toBe("1,2 km");
  });
  it("Suchtreffer bekommt eine passende Detailstufe", () => {
    expect(placeFromSearch({ lat: "48.85", lon: "2.29", display_name: "Eiffelturm, Paris", type: "tower" }).zoom).toBe(17);
    expect(placeFromSearch({ lat: "48.85", lon: "2.35", display_name: "Paris, Frankreich", addresstype: "city" }).zoom).toBe(13.5);
  });
});

const base = {
  version: 8, glyphs: "https://example/{fontstack}/{range}.pbf",
  sources: { openmaptiles: { type: "vector", url: "https://example/planet" } },
  layers: [
    { id: "background", type: "background", paint: { "background-color": "#111" } },
    { id: "water", type: "fill", source: "openmaptiles", "source-layer": "water", paint: { "fill-color": "#00f" } },
    { id: "landuse_park", type: "fill", source: "openmaptiles", "source-layer": "landuse", paint: {} },
    { id: "building", type: "fill", source: "openmaptiles", "source-layer": "building", paint: { "fill-color": "#333" } },
    { id: "highway_major", type: "line", source: "openmaptiles", "source-layer": "transportation", paint: { "line-color": "#fff" } },
    { id: "place_city", type: "symbol", source: "openmaptiles", "source-layer": "place", layout: {}, paint: {} },
  ],
} as unknown as StyleSpecification;

describe("Cyber-Map-Stil", () => {
  it("legt das Satellitenbild über die Grundflächen, unter Gebäude, Straßen und Beschriftungen", () => {
    const style = buildStyle(base);
    const ids = style.layers.map((l) => l.id);
    expect(ids.indexOf("jarvis-satellit")).toBe(ids.indexOf("landuse_park") + 1);
    expect(ids.indexOf("jarvis-satellit")).toBeLessThan(ids.indexOf("building"));
    expect(style.layers.find((l) => l.id === "building")?.type).toBe("fill-extrusion"); // 3D statt deckender Fläche
    expect((style as any).projection).toEqual({ type: "globe" });
    expect((style.layers.find((l) => l.id === "highway_major") as any).paint["line-color"]).toBe(COLORS.line);
    expect((style.sources as any).satellit.attribution).toContain("Archivbild");
    expect(base.layers.length).toBe(6); // Eingabe unverändert
  });
  it("Ebenen: Auto blendet ab Zoom 10,5 bis 14 ein, Satellit/Straße fest", () => {
    expect(satelliteOpacity("auto")).toEqual(["interpolate", ["linear"], ["zoom"], 10.5, 0, 14, 1]);
    expect(satelliteOpacity("satellit")).toBe(1);
    expect(satelliteOpacity("strasse")).toBe(0);
  });
});

describe("Sprachbefehle für die Weltansicht", () => {
  it("öffnet die Weltansicht mit Ziel", () => {
    expect(classifyUtterance("Flieg nach London")).toMatchObject({ kind: "open", view: "world", query: "London" });
    expect(classifyUtterance("Zeig mir Wall Street auf der Karte")).toMatchObject({ kind: "open", view: "world", query: "Wall Street" });
    expect(classifyUtterance("Öffne die Weltkarte")).toMatchObject({ kind: "open", view: "world" });
  });
  it("lässt Nachrichtenfragen unverändert", () => {
    expect(classifyUtterance("Was gibt es Neues in London?")).toMatchObject({ kind: "open", view: "news" });
    expect(classifyUtterance("Zeig mir nicht die Karte")).toEqual({ kind: "none" });
  });
});
