// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CommandCenter } from "./CommandCenter";
import { MemoryCenter } from "./MemoryCenter";
import { Telemetry } from "./telemetry";

(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root | null = null;
afterEach(() => { act(() => root?.unmount()); root = null; document.body.innerHTML = ""; vi.unstubAllGlobals(); });

const mount = async (node: React.ReactNode) => {
  const host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => { root!.render(node); await new Promise((r) => setTimeout(r, 0)); });
  return host;
};
const base = {
  telemetry: new Telemetry(), audio: null, role: "owner" as const, connected: true, micOn: false, onMicChange: vi.fn(),
  muted: false, onMutedChange: vi.fn(), onAsk: vi.fn(), onStop: vi.fn(), onLog: vi.fn(), automations: null, approvals: [], onOpenApprovals: vi.fn(),
};

describe("Memory v1 im Gespräch", () => {
  it("zeigt an, welche gespeicherten Erinnerungen für eine Antwort verwendet wurden – nur bei dieser Antwort", async () => {
    const onOpenMemory = vi.fn();
    const host = await mount(<CommandCenter {...base} onOpenMemory={onOpenMemory} transcripts={[
      { id: "u1", role: "user", text: "Wer ist mein Hauptkunde?", stream_id: "s1", final: true },
      { id: "a1", role: "assistant", text: "Die Kanzlei Weber.", stream_id: "s1", final: true },
      { id: "u2", role: "user", text: "Danke.", stream_id: "s2", final: true },
      { id: "a2", role: "assistant", text: "Gern.", stream_id: "s2", final: true },
    ]} memoryUse={{ s1: [{ id: "f1", inhalt: "Mein Hauptkunde ist die Kanzlei Weber." }] }} />);
    const notes = host.querySelectorAll(".cc-memory-used");
    expect(notes).toHaveLength(1);
    expect(notes[0].textContent).toContain("1 gespeicherte Erinnerung verwendet");
    expect(notes[0].textContent).toContain("Mein Hauptkunde ist die Kanzlei Weber.");
    await act(async () => notes[0].querySelector<HTMLButtonElement>("button")!.click());
    expect(onOpenMemory).toHaveBeenCalled();
  });

  it("macht den Cloud-Versand in den Einstellungen klar und schaltet ihn ab", async () => {
    const saved: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init: any = {}) => {
      saved.push(JSON.parse(init.body));
      return { ok: true, status: 200, json: async () => ({ ...settings, erinnerungen_an_cloud: false }) };
    }));
    const settings = { modell: "claude-sonnet-5-5", modell_key: "sonnet", modelle: ["sonnet"], stimme: "x", tempo: 1, stimmen: [],
      elevenlabs: true, elevenlabs_problem: null, sprachkern_verfuegbar: false, sprachkern_problem: null, eleven_tts_model: "x",
      eleven_tts_modelle: [], google: false, hubspot: false, anbieter: "Anthropic (Claude)", erinnerungen_an_cloud: true, anbieter_cloud: true };
    const host = await mount(<CommandCenter {...base} transcripts={[]} settings={settings} />);
    await act(async () => [...host.querySelectorAll<HTMLButtonElement>(".cc-bar button")].find((b) => b.textContent === "Einstellungen")!.click());
    const box = host.querySelector(".cc-memory-privacy")!;
    expect(box.textContent).toContain("Anthropic (Claude)");
    expect(box.textContent).toContain("höchstens fünf");
    expect(box.textContent).toContain("nie den ganzen Speicher");
    const toggle = box.querySelector<HTMLInputElement>('input[type="checkbox"]')!;
    expect(toggle.checked).toBe(true);
    await act(async () => { toggle.click(); await new Promise((r) => setTimeout(r, 0)); });
    expect(saved).toEqual([{ erinnerungen_an_cloud: false }]);
  });
});

describe("Gedächtnis-Center (Memory v1)", () => {
  const center = (wissen: unknown[]) => ({
    suche: "", wissen, archiv: [], stand: "2026-11-11T01:00:00", notizdateien: [],
    zahlen: { facts: wissen.length, episodes: 3, events: 0, path: "/Users/x/.jarvis/memory.sqlite3", ausstehend: 0, fehlgeschlagen: 0,
      widersprueche: 0, archiv_seit: null, archiv_nach_status: {}, archiv_nach_kanal: {} },
  });
  const fact = (id: string, content: string, consent: string) => ({
    id, content, category: "note", source: "user", client: "chat", created_at: "2026-11-11T01:00:00", updated_at: "2026-11-11T01:00:00",
    valid_until: null, confidence: 1, consent, confirmed_at: null, origin_episode: "", conflicts: [], gueltig: true, versionen: 0,
  });

  it("zeigt Vorschläge als noch nicht verwendet und löscht alle erst nach zweitem Klick", async () => {
    const posts: { url: string; body: any }[] = [];
    let data = center([fact("f1", "Ich trinke Tee", "explicit"), fact("f2", "Ich ziehe vielleicht um", "vorschlag")]);
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: any = {}) => {
      if ((init.method ?? "GET") === "POST") {
        posts.push({ url, body: JSON.parse(init.body) });
        data = center([]);
        return { ok: true, status: 200, json: async () => ({ ok: true, geloescht: 2 }) };
      }
      return { ok: true, status: 200, json: async () => data };
    }));
    const host = await mount(<MemoryCenter canEdit />);
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); }); // Laden startet nach einem Tick
    const rows = [...host.querySelectorAll(".mc-fact")];
    expect(rows[0].textContent).toContain("aktiv");
    expect(rows[1].textContent).toContain("Vorschlag – wird erst nach deiner Bestätigung verwendet");
    expect([...rows[0].querySelectorAll("button")].some((b) => b.textContent === "Bestätigen")).toBe(false);
    expect([...rows[1].querySelectorAll("button")].some((b) => b.textContent === "Bestätigen")).toBe(true);
    expect(host.querySelector(".mc-explain summary")!.textContent).toBe("Was wird wo gespeichert?");
    expect(host.querySelector(".mc-explain")!.textContent).toContain("nur was du ausdrücklich speicherst");
    await act(async () => [...host.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent === "Alle Erinnerungen löschen")!.click());
    expect(posts).toEqual([]);
    await act(async () => {
      [...host.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent === "Ja, alle endgültig löschen")!.click();
      await new Promise((r) => setTimeout(r, 0));
    });
    expect(posts).toEqual([{ url: "/api/gedaechtnis/alle_loeschen", body: { bestaetigt: true } }]);
    expect(host.textContent).toContain("Alle 2 Erinnerungen gelöscht");
  });
});

describe("Ägyptisch im Gespräch", () => {
  it("zeigt arabische Antworten von rechts nach links und deutsche normal", async () => {
    const host = await mount(<CommandCenter {...base} transcripts={[
      { id: "u1", role: "user", text: "إزيك يا جارفس؟", stream_id: "s1", final: true },
      { id: "a1", role: "assistant", text: "تمام يا ماستر، عندك ميتنج مع Weber الساعة 3.", stream_id: "s1", final: true },
      { id: "a2", role: "assistant", text: "Alles klar, Master.", stream_id: "s2", final: true },
    ]} />);
    const spans = [...host.querySelectorAll(".cc-line > span")];
    expect(spans.map((s) => s.getAttribute("dir"))).toEqual(["rtl", "rtl", "auto"]);
    expect(host.querySelector(".cc-transport input")!.getAttribute("dir")).toBe("auto");
  });

  it("bietet die Sprache in den Einstellungen an und speichert sie", async () => {
    const saved: unknown[] = [];
    vi.stubGlobal("fetch", vi.fn(async (_url: string, init: any = {}) => {
      if (init.body) saved.push(JSON.parse(init.body));
      return { ok: true, status: 200, json: async () => ({ ...settings, sprache: "arz" }) } as Response;
    }));
    const settings = { modell: "claude-sonnet-5-5", modell_key: "sonnet", modelle: ["sonnet"], stimme: "edge:de-DE-ConradNeural", tempo: 1,
      stimmen: [{ id: "edge:de-DE-ConradNeural", name: "Conrad" }, { id: "edge:ar-EG-ShakirNeural", name: "Shakir – ägyptisch" }],
      elevenlabs: false, elevenlabs_problem: null, sprachkern_verfuegbar: false, sprachkern_problem: null, eleven_tts_model: "x", eleven_tts_modelle: [],
      google: false, hubspot: false, sprache: "de" as const, stimme_arabisch: "edge:ar-EG-ShakirNeural",
      sprachen: [{ id: "de", name: "Deutsch" }, { id: "arz", name: "Ägyptisch (Masri)" }, { id: "auto", name: "Automatisch" }] };
    const { SettingsDialog } = await import("./SettingsDialog");
    const host = await mount(<SettingsDialog open onClose={vi.fn()} prefs={{ pause: 1.2, barge: "frei", talkMode: "frei" }} onPrefs={vi.fn()}
      settings={settings} onSettings={vi.fn()} onReset={vi.fn()} canEdit />);
    const label = [...host.querySelectorAll("label")].find((l) => l.textContent!.startsWith("Sprache"))!;
    const select = label.querySelector("select")!;
    expect([...select.options].map((o) => o.textContent)).toEqual(["Deutsch", "Ägyptisch (Masri)", "Automatisch"]);
    await act(async () => { select.value = "arz"; select.dispatchEvent(new Event("change", { bubbles: true })); await new Promise((r) => setTimeout(r, 0)); });
    expect(saved).toContainEqual({ sprache: "arz" });
  });
});
