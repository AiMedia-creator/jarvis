// @vitest-environment happy-dom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import CommandView from "../views/CommandView";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;

const order = { id: "A-1", version: 3, titel: "Website Kanzlei", kunde: "Weber", projekt: "", prioritaet: "hoch",
  verantwortlich: "", frist: "", status: "eingang", position: 1000, checkliste: [], abhaengigkeiten: [], quelle: "",
  notiz: "", jobs: [], runs: [], erstellt: 0, geaendert: 0 };
const approval = { id: "abc123", werkzeug: "mail_senden", status: "WAITING_FOR_APPROVAL", status_text: "wartet",
  ergebnis: "", erstellt: Date.now() / 1000, laeuft_ab: Date.now() / 1000 + 1500, job: { id: "j1", status: "WAITING", titel: "Mail" },
  graph: "wartet", vorschau: { titel: "E-Mail senden", ziel: "Gmail", zeilen: [{ feld: "An", wert: "kunde@x.de" }, { feld: "Text", wert: "Hallo <b>roh</b>" }] } };

type Call = { url: string; method: string; headers: Record<string, string>; body: any };
function mockFetch(handler: (call: Call) => { status?: number; body: any } | undefined) {
  const calls: Call[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: any = {}) => {
    const call = { url, method: init.method ?? "GET", headers: init.headers ?? {}, body: init.body ? JSON.parse(init.body) : null };
    calls.push(call);
    const custom = handler(call);
    const fallback: { status?: number; body: any } = url.startsWith("/api/orders") ? { body: { spalten: [], auftraege: [order] } }
      : url.startsWith("/api/approvals") ? { body: { freigaben: [approval] } }
      : { body: { status: "ok", zeitzone: "Europe/Berlin", termine: [], konflikte: [] } };
    const r = custom ?? fallback;
    return { ok: (r.status ?? 200) < 400, status: r.status ?? 200, json: async () => r.body };
  }));
  return calls;
}

async function render(props: Partial<Parameters<typeof CommandView>[0]> = {}) {
  const host = document.createElement("div");
  document.body.append(host);
  const root = createRoot(host);
  const onAsk = vi.fn();
  await act(async () => {
    root.render(<CommandView role="owner" connected onBack={() => {}} onAsk={onAsk} onView={() => {}} {...props} />);
  });
  await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return { host, root, onAsk };
}
const click = async (el: Element | null | undefined) => {
  expect(el).toBeTruthy();
  await act(async () => { (el as HTMLElement).click(); await new Promise((r) => setTimeout(r, 0)); });
};
const button = (host: HTMLElement, text: string) =>
  [...host.querySelectorAll("button")].find((b) => b.textContent?.includes(text) || b.getAttribute("aria-label")?.includes(text));

afterEach(() => { document.body.innerHTML = ""; vi.unstubAllGlobals(); });

describe("Kommandozentrale", () => {
  it("Freigabe zeigt die Vorschau unverändert und führt erst nach Bestätigung aus", async () => {
    const calls = mockFetch(() => undefined);
    const { host } = await render({ query: "freigaben" });
    expect(host.textContent).toContain("Hallo <b>roh</b>"); // als Text, nicht als HTML
    expect(host.querySelector("b")).toBeNull();
    await click(button(host.querySelector(".cc-work")!, "Freigeben …"));
    expect(calls.some((c) => c.method === "POST")).toBe(false);
    await click(button(host.querySelector(".cc-work")!, "Ja, ausführen"));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.url).toBe("/api/approvals/abc123/freigeben");
    expect(post.headers["X-Jarvis"]).toBe("1");
  });

  it("Beobachter sehen Freigaben, können aber nichts auslösen oder verschieben", async () => {
    mockFetch(() => undefined);
    const { host } = await render({ query: "freigaben", role: "observer" });
    expect(button(host, "Freigeben")).toBeUndefined();
    await click(button(host, "Aufträge"));
    expect(button(host, "nach rechts verschieben")).toBeUndefined();
  });

  it("Verschieben sendet Version und request_id, rollt bei Konflikt zurück und fragt Jarvis nie", async () => {
    const calls = mockFetch((c) => c.method === "PATCH"
      ? { status: 409, body: { fehler: "geändert", aktuell: { ...order, version: 4, status: "erledigt" } } } : undefined);
    const { host, onAsk } = await render({ query: "auftraege" });
    await click(button(host, "Website Kanzlei nach rechts verschieben"));
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(patch.body).toMatchObject({ version: 3, aenderungen: { status: "klaerung" } });
    expect(patch.body.request_id).toMatch(/^ui-/);
    expect(host.querySelector('[aria-label="Spalte Erledigt"]')?.textContent).toContain("Website Kanzlei");
    expect(host.textContent).toContain("inzwischen geändert");
    expect(onAsk).not.toHaveBeenCalled();
  });

  it("zeigt offline den letzten Stand mit ehrlichem Hinweis", async () => {
    mockFetch((c) => (c.url.startsWith("/api/orders") ? { status: 502, body: { fehler: "Bridge nicht erreichbar." } } : undefined));
    const { host } = await render();
    expect(host.textContent).toContain("Bridge nicht erreichbar.");
    expect(host.textContent).toContain("letzte bekannte Stand");
  });

  it("Heute: zeigt, was den Nutzer braucht, und übergibt einen Auftrag direkt an Jarvis", async () => {
    mockFetch(() => undefined);
    const { host, onAsk } = await render();
    const queue = host.querySelector(".td-queue")!;
    expect(host.querySelector(".td-head h2")!.textContent).toContain("Eine Sache braucht dich.");
    expect(queue.textContent).toContain("E-Mail senden");
    expect(queue.textContent).toContain("Ziel: Gmail");
    const input = host.querySelector<HTMLTextAreaElement>(".td-command textarea")!;
    await click(button(host.querySelector(".td-suggest")!, "Tagesbriefing"));
    expect(input.value).toContain("Was steht heute an?");
    expect(onAsk).not.toHaveBeenCalled(); // Vorschläge füllen nur das Feld
    await act(async () => { host.querySelector<HTMLFormElement>(".td-command")!.requestSubmit(); await new Promise((r) => setTimeout(r, 0)); });
    expect(onAsk).toHaveBeenCalledWith("Was steht heute an? Gib mir die drei wichtigsten Punkte.");
    expect(host.querySelector(".cc-center .cc-transport")).not.toBeNull(); // weiter im Command Center
  });

  it("Heute: führt vom Eintrag direkt zur Entscheidung und benennt nicht geprüfte Quellen", async () => {
    mockFetch((c) => (c.url.startsWith("/api/agenten") ? { status: 502, body: { fehler: "aus" } } : undefined));
    const { host } = await render();
    expect(host.querySelector(".td-unchecked")!.textContent).toContain("Agenten");
    await click(button(host.querySelector(".td-queue")!, "Entscheiden"));
    expect(button(host.querySelector(".cc-work")!, "Freigeben …")).toBeTruthy();
  });

  it("Heute: Beobachter können nichts übergeben", async () => {
    mockFetch(() => undefined);
    const { host } = await render({ role: "observer" });
    expect(host.querySelector<HTMLTextAreaElement>(".td-command textarea")!.disabled).toBe(true);
    expect(host.querySelector(".td-suggest")).toBeNull();
  });

  it("respektiert reduzierte Bewegung und kurze Übergänge", () => {
    const css = readFileSync("src/command/command.css", "utf8");
    expect(css).toContain("prefers-reduced-motion: reduce");
    expect(css).toMatch(/--cc-t: 1[6-9]\dms|--cc-t: 2[0-2]\dms/);
  });
});
