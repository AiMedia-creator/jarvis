// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { describe, expect, it, vi } from "vitest";
import { CapabilityPanel } from "./CapabilityPanel";
import type { Capability, CapabilityRun } from "./api";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const cap: Capability = {
  name: "auftrag_anlegen", titel: "Auftrag erfassen", beschreibung: "…", berechtigung: "lokal",
  berechtigung_text: "ändert nur lokale Jarvis-Daten (keine Freigabe nötig)", abbrechbar: true,
  eingabe: { properties: { titel: {}, kunde: {} }, required: ["titel"] },
};
const run = (over: Partial<CapabilityRun>): CapabilityRun => ({
  id: "job1", faehigkeit: "auftrag_anlegen", quelle: "web", status: "COMPLETED", start: 1_800_000_000, ende: 1_800_000_000.05,
  eingabe: { titel: "Angebot Weber" }, ergebnis: { auftrag_id: "A-123", text: "ok" }, fehler: null, abbrechbar: false, ...over,
});

async function render(runs: CapabilityRun[], onAction = vi.fn(), onOpenOrder = vi.fn()) {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  await act(async () => root.render(<CapabilityPanel capabilities={[cap]} runs={runs} error={null} canEdit busy={null}
    onAction={onAction} onOpenOrder={onOpenOrder} />));
  return { host, root, onAction, onOpenOrder };
}

describe("Fähigkeiten im Command Center", () => {
  it("zeigt Berechtigung, Lauf und Ergebnis mit Link zum Auftrag", async () => {
    const { host, root, onOpenOrder } = await render([run({})]);
    expect(host.textContent).toContain("Auftrag erfassen");
    expect(host.textContent).toContain("lokal · ohne Freigabe");
    expect(host.textContent).toContain("erledigt");
    const open = [...host.querySelectorAll("button")].find((b) => b.textContent === "A-123 öffnen")!;
    await act(async () => open.click());
    expect(onOpenOrder).toHaveBeenCalledWith("A-123");
    act(() => root.unmount());
  });

  it("bietet Abbrechen nur für laufende und Wiederholen nur für gescheiterte Läufe", async () => {
    const { host, root, onAction } = await render([
      run({ id: "a", status: "RUNNING", abbrechbar: true, ergebnis: null, ende: null }),
      run({ id: "b", status: "FAILED", fehler: "Auftrag nicht angelegt: Titel fehlt", ergebnis: null }),
    ]);
    const buttons = [...host.querySelectorAll("button")].map((b) => b.textContent);
    expect(buttons).toEqual(["Abbrechen", "Wiederholen"]);
    expect(host.textContent).toContain("Titel fehlt");
    await act(async () => (host.querySelector("button") as HTMLButtonElement).click());
    expect(onAction).toHaveBeenCalledWith("a", "abbrechen");
    act(() => root.unmount());
  });

  it("zeigt ohne Lauf keine erfundenen Ergebnisse", async () => {
    const { host, root } = await render([]);
    expect(host.textContent).toContain("Noch kein Lauf");
    expect(host.querySelectorAll("button")).toHaveLength(0);
    act(() => root.unmount());
  });
});
