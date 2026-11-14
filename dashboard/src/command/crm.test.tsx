// @vitest-environment happy-dom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { readFileSync } from "node:fs";
import { AgentCenter, isImportant, isJob } from "./AgentCenter";
import type { AgentCenterData, AgentJob, Lead, LeadOverview } from "./api";
import { setOwnerProof } from "./api";
import { CrmPanel, PAGE_SIZE, validateLead } from "./CrmPanel";
import { SkillsOverview } from "./SkillsOverview";

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
const tick = () => act(async () => { await new Promise((r) => setTimeout(r, 0)); });
const button = (scope: ParentNode, text: string) => [...scope.querySelectorAll("button")].find((b) => b.textContent?.trim().startsWith(text)) as HTMLButtonElement;
const key = (k: string) => act(async () => { window.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true })); await new Promise((r) => setTimeout(r, 0)); });

const STATUS = [
  { id: "neu", titel: "Neu", offen: true, ergebnis: false, ton: "neutral" },
  { id: "nicht_erreicht", titel: "Nicht erreicht", offen: true, ergebnis: true, ton: "neutral" },
  { id: "mailbox", titel: "Mailbox", offen: true, ergebnis: true, ton: "warn" },
  { id: "follow_up", titel: "Follow-up", offen: true, ergebnis: true, ton: "arbeit" },
  { id: "meeting_gebucht", titel: "Meeting gebucht", offen: false, ergebnis: true, ton: "ok" },
  { id: "kein_interesse", titel: "Kein Interesse", offen: false, ergebnis: true, ton: "danger" },
] as const;
const lead = (id: string, firma: string, status = "neu", extra: Partial<Lead> = {}): Lead => ({
  id, version: 1, erstellt: 1, geaendert: 1, status, firma, name: "Frau Beispiel", rolle: "Geschäftsführung", telefon: "+49 89 123456",
  email: "", website: "", ort: "München", quelle: "", telefon_status: "geschaeftlich", projekt_id: "", notiz: "", naechster_schritt: "",
  followup_am: "", termin_beginn: "", termin_ende: "", letzte_aktivitaet: null, letzte_aktivitaet_text: "", anrufe: 0, ...extra,
});

/** Kleiner Lead-Server im Speicher: dieselben Regeln wie das Backend, soweit die Oberfläche sie braucht. */
function server(initial: Lead[]) {
  const leads = [...initial];
  const calls: { method: string; url: string; body: any; headers: Record<string, string> }[] = [];
  // Wie das Backend: Zahlen über den ganzen Bestand, Seite aus einem eingefrorenen Schnappschuss der Reihenfolge.
  const snapshots = new Map<string, string[]>();
  const overview = (params: URLSearchParams): LeadOverview => {
    const status = params.get("status") ?? "", q = (params.get("q") ?? "").toLowerCase();
    const limit = Number(params.get("limit") ?? 50), um = params.get("um") ?? "";
    let snapshot = params.get("snapshot") ?? "";
    const fresh = !snapshots.has(`${snapshot}|${status}|${q}`);
    if (fresh) {
      snapshot = `S-${snapshots.size + 1}`;
      snapshots.set(`${snapshot}|${status}|${q}`, leads.filter((l) => (!status || (status === "offen" ? STATUS.find((s) => s.id === l.status)!.offen : l.status === status))
        && (!q || l.firma.toLowerCase().includes(q))).map((l) => l.id));
    }
    const order = snapshots.get(`${snapshot}|${status}|${q}`)!;
    let offset = Number(params.get("offset") ?? 0);
    const at = um ? order.indexOf(um) : -1;
    if (at >= 0) offset = Math.floor(at / limit) * limit;
    return {
      status: [...STATUS], leads: order.slice(offset, offset + limit).map((id) => leads.find((l) => l.id === id)!),
      zahlen: Object.fromEntries(STATUS.map((s) => [s.id, leads.filter((l) => l.status === s.id).length])), gesamt: leads.length,
      offen: leads.filter((l) => STATUS.find((s) => s.id === l.status)!.offen).length,
      followups_faellig: leads.filter((l) => l.status === "follow_up" && l.followup_am && l.followup_am <= "2026-11-11").length,
      heute: "2026-11-11", csv_spalten: ["firma", "name", "rolle", "telefon", "email", "website", "notiz"],
      treffer: order.length, offset, limit, snapshot, snapshot_neu: fresh, um_position: at >= 0 ? at : null, csv_max_bytes: 1_000_000, csv_max_zeilen: 2000,
    };
  };
  const json = (status: number, body: unknown) => ({ ok: status < 400, status, json: async () => body }) as Response;
  vi.stubGlobal("fetch", vi.fn(async (url: string, init: any = {}) => {
    const method = init.method ?? "GET";
    const body = typeof init.body === "string" ? JSON.parse(init.body) : init.body ?? null;
    calls.push({ method, url, body, headers: init.headers ?? {} });
    if (url.startsWith("/api/leads/import")) return json(200, { neu: 3, importiert: url.includes("ausfuehren=true") ? 3 : 0, fehler_gesamt: 0, duplikate_gesamt: 0, fehler: [], duplikate: [], zeilen: 3 });
    if (url.startsWith("/api/leads/session")) return json(200, { seit: 0, calls: calls.filter((c) => c.url.endsWith("/ergebnis")).length, nach_status: {}, letzter: null, stand: 0 });
    if (url.startsWith("/api/leads?")) return json(200, overview(new URL(url, "http://x").searchParams));
    const match = /^\/api\/leads\/([^/]+)(\/ergebnis)?$/.exec(url);
    if (match && method === "GET") return json(200, { lead: leads.find((l) => l.id === match[1]), aktivitaeten: [] });
    if (match?.[2] && method === "POST") {
      const i = leads.findIndex((l) => l.id === match[1]);
      if (body.version !== leads[i].version) return json(409, { fehler: "Der Lead wurde inzwischen geändert.", aktuell: leads[i] });
      if (body.status === "follow_up" && !body.followup_am) return json(400, { fehler: "Für ein Follow-up ist ein Datum nötig." });
      leads[i] = { ...leads[i], status: body.status, version: leads[i].version + 1, followup_am: body.followup_am ?? "", termin_beginn: body.termin_beginn ?? "",
        termin_ende: body.termin_beginn ? `${body.termin_beginn.slice(0, 11)}10:30` : "", anrufe: leads[i].anrufe + 1 };
      return json(200, { lead: leads[i], aktivitaet_id: "LA-1", wiederholt: false,
        ...(body.kalender ? { kalender: { status: "vorlage", hinweis: "Kalender-Vorlage erstellt. Der Termin wird erst nach deiner Freigabe angelegt." } } : {}) });
    }
    if (url === "/api/leads" && method === "POST") {
      const dup = leads.find((l) => l.telefon === body.felder.telefon);
      if (dup) return json(409, { fehler: "Diese Telefonnummer gehört schon zu einem Lead.", duplikat: dup, hart: true });
      const created = lead(`L-${leads.length + 1}`, body.felder.firma, "neu", body.felder);
      leads.push(created);
      return json(200, { lead: created });
    }
    return json(404, { fehler: "nicht gefunden" });
  }));
  return { calls, leads };
}

describe("Cold-Call-CRM", () => {
  it("zeigt Leads mit einheitlichem Status, blättert mit Pfeilen in der gefilterten Reihenfolge", async () => {
    server([lead("L-1", "Alpha AG"), lead("L-2", "Beta GmbH", "mailbox"), lead("L-3", "Gamma KG", "kein_interesse")]);
    const host = await mount(<CrmPanel canEdit auswertung={<div />} onOpenApprovals={vi.fn()} />);
    await tick();
    const rows = [...host.querySelectorAll(".crm-table tbody tr")];
    expect(rows.map((r) => r.querySelector("strong")!.textContent)).toEqual(["Alpha AG", "Beta GmbH", "Gamma KG"]);
    expect(rows[1].querySelector(".cc-state")!.textContent).toBe("Mailbox");
    const filters = [...host.querySelectorAll(".crm-filter > button")].map((b) => b.textContent!.replace(/\s+/g, " ").trim());
    for (const label of ["Nicht erreicht", "Meeting gebucht", "Kein Interesse", "Mailbox", "Follow-up"]) expect(filters.some((f) => f.startsWith(label))).toBe(true);

    await act(async () => rows[0].querySelector<HTMLButtonElement>(".crm-open")!.click());
    await tick();
    const detail = () => host.querySelector(".crm-detail")!;
    expect(detail().querySelector("h2")!.textContent).toBe("Alpha AG");
    expect(detail().querySelector(".crm-pos")!.textContent).toBe("1/3");
    expect(detail().querySelector<HTMLAnchorElement>(".crm-tel")!.getAttribute("href")).toBe("tel:+4989123456");
    expect(detail().textContent).toContain("Letzte Aktivität");
    expect(detail().textContent).toContain("Nächster Schritt");
    await act(async () => detail().querySelector<HTMLButtonElement>('[aria-label^="Nächster Lead"]')!.click());
    expect(detail().querySelector("h2")!.textContent).toBe("Beta GmbH");
    expect(detail().querySelector(".crm-facts .cc-state")!.textContent).toBe("Mailbox"); // Status im Detail wie in der Liste
    await key("ArrowLeft");
    expect(detail().querySelector("h2")!.textContent).toBe("Alpha AG");
    await key("ArrowLeft"); // am Anfang weiter zum letzten der Liste
    expect(detail().querySelector("h2")!.textContent).toBe("Gamma KG");

    // Filter „Anrufliste“: Navigation folgt der gefilterten Reihenfolge (Gamma ist nicht mehr dabei)
    await act(async () => button(host.querySelector(".crm-filter")!, "Anrufliste").click());
    await tick();
    await act(async () => host.querySelector<HTMLButtonElement>(".crm-table .crm-open")!.click());
    expect(detail().querySelector(".crm-pos")!.textContent).toBe("1/2");
    await key("ArrowRight"); await key("ArrowRight");
    expect(detail().querySelector("h2")!.textContent).toBe("Alpha AG");
  });

  it("erfasst ein Ergebnis mit einem Klick, verlangt beim Follow-up ein Datum und beim Meeting einen Termin", async () => {
    const api = server([lead("L-1", "Alpha AG")]);
    const onOpenApprovals = vi.fn();
    const host = await mount(<CrmPanel canEdit auswertung={<div />} onOpenApprovals={onOpenApprovals} focusLead="L-1" />);
    await tick();
    const detail = () => host.querySelector(".crm-detail")!;
    await act(async () => button(detail(), "Mailbox").click());
    await tick();
    const first = api.calls.find((c) => c.url === "/api/leads/L-1/ergebnis")!;
    expect(first.body).toMatchObject({ status: "mailbox", version: 1 });
    expect(first.body.request_id).toMatch(/^ui-/);
    expect(host.querySelector(".crm-ok")!.textContent).toContain("Gespeichert: Mailbox · Alpha AG");

    await act(async () => button(detail(), "Follow-up").click());
    const date = detail().querySelector<HTMLInputElement>('.crm-draft input[type="date"]')!;
    expect(date.required).toBe(true);
    expect(date.value).toMatch(/^\d{4}-\d{2}-\d{2}$/); // Vorschlag: morgen
    await act(async () => { detail().querySelector<HTMLFormElement>(".crm-draft")!.requestSubmit(); await new Promise((r) => setTimeout(r, 0)); });
    const follow = api.calls.filter((c) => c.url === "/api/leads/L-1/ergebnis")[1];
    expect(follow.body).toMatchObject({ status: "follow_up", followup_am: date.value, version: 2 });

    await act(async () => button(detail(), "Meeting gebucht").click());
    expect(detail().querySelector<HTMLInputElement>('.crm-draft input[type="datetime-local"]')!.required).toBe(true);
    await act(async () => { detail().querySelector<HTMLFormElement>(".crm-draft")!.requestSubmit(); await new Promise((r) => setTimeout(r, 0)); });
    const meeting = api.calls.filter((c) => c.url === "/api/leads/L-1/ergebnis")[2];
    expect(meeting.body).toMatchObject({ status: "meeting_gebucht", kalender: true, version: 3 });
    expect(meeting.body.termin_beginn).toMatch(/T10:00$/);
    expect(detail().querySelector(".crm-cal")!.textContent).toContain("erst nach deiner Freigabe");
    await act(async () => button(detail().querySelector(".crm-cal")!, "Zur Freigabe").click());
    expect(onOpenApprovals).toHaveBeenCalled();
  });

  it("zeigt bei einem Versionskonflikt den aktuellen Stand statt still zu überschreiben", async () => {
    const api = server([lead("L-1", "Alpha AG")]);
    const host = await mount(<CrmPanel canEdit auswertung={<div />} onOpenApprovals={vi.fn()} focusLead="L-1" />);
    await tick();
    api.leads[0] = { ...api.leads[0], version: 5, status: "nicht_erreicht" }; // inzwischen woanders geändert
    await act(async () => button(host.querySelector(".crm-detail")!, "Kein Interesse").click());
    await tick();
    expect(host.querySelector(".crm-detail [role=alert]")!.textContent).toContain("inzwischen geändert");
    expect(api.leads[0].status).toBe("nicht_erreicht");
  });

  it("führt durch die Call-Session: Fortschritt, nächster Lead, Zifferntaste, automatisch weiter", async () => {
    const api = server([lead("L-1", "Alpha AG"), lead("L-2", "Beta GmbH")]);
    const host = await mount(<CrmPanel canEdit auswertung={<div />} onOpenApprovals={vi.fn()} />);
    await tick();
    await act(async () => button(host, "Aufgaben starten").click());
    await tick();
    expect(host.querySelector(".crm-start")!.textContent).toContain("20 Calls pro Stunde");
    await act(async () => button(host.querySelector(".crm-start")!, "Session starten").click());
    await tick();
    expect(host.querySelector(".crm-meter-num")!.textContent).toBe("0/20");
    expect(host.querySelector(".crm-meter [role=progressbar]")!.getAttribute("aria-valuenow")).toBe("0");
    expect(host.querySelector(".crm-detail h2")!.textContent).toBe("Alpha AG");
    expect(host.querySelector(".crm-meter-next")!.textContent).toContain("Beta GmbH");
    await key("1"); // 1 = Nicht erreicht
    await tick();
    expect(api.calls.find((c) => c.url === "/api/leads/L-1/ergebnis")!.body.status).toBe("nicht_erreicht");
    expect(host.querySelector(".crm-detail h2")!.textContent).toBe("Beta GmbH"); // automatisch zum nächsten Lead
    expect(host.querySelector(".crm-meter-num")!.textContent).toBe("1/20"); // gezählt wird nur Gespeichertes
    expect(host.textContent).toContain("Jarvis wählt nicht selbst");
    await act(async () => button(host.querySelector(".crm-meter")!, "Session beenden").click());
    expect(host.querySelector(".crm-start")).not.toBeNull();
  });

  it("legt Leads an, nennt Duplikate und bietet den vorhandenen Lead an", async () => {
    const api = server([lead("L-1", "Alpha AG")]);
    const host = await mount(<CrmPanel canEdit auswertung={<div />} onOpenApprovals={vi.fn()} />);
    await tick();
    await act(async () => button(host, "Lead anlegen").click());
    const form = host.querySelector<HTMLFormElement>('form[aria-label="Neuen Lead anlegen"]')!;
    const set = async (label: string, value: string) => {
      const input = [...form.querySelectorAll("label")].find((l) => l.textContent!.startsWith(label))!.querySelector("input")!;
      await act(async () => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
        input.dispatchEvent(new Event("input", { bubbles: true }));
      });
    };
    await act(async () => { form.requestSubmit(); await new Promise((r) => setTimeout(r, 0)); });
    expect(form.querySelector("[role=alert]")!.textContent).toContain("Firma oder Name ist nötig");
    await set("Firma", "Doppelt GmbH"); await set("Telefon", "+49 89 123456");
    await act(async () => { form.requestSubmit(); await new Promise((r) => setTimeout(r, 0)); });
    expect(form.querySelector(".crm-dup")!.textContent).toContain("Alpha AG");
    expect(button(form.querySelector(".crm-dup")!, "Trotzdem")).toBeUndefined(); // gleiche Nummer: nie doppelt
    expect(api.leads).toHaveLength(1);
    await set("Telefon", "+49 30 999");
    await act(async () => { form.requestSubmit(); await new Promise((r) => setTimeout(r, 0)); });
    await tick();
    expect(api.leads).toHaveLength(2);
    expect(host.querySelector(".crm-ok")!.textContent).toContain("Lead angelegt: Doppelt GmbH");
    // Abschluss ist sichtbar und führt weiter: öffnen, weiteren anlegen oder fertig
    const done = host.querySelector('section[aria-label="Lead angelegt"]')!;
    expect(done.querySelector(".nl-preview strong")!.textContent).toBe("Doppelt GmbH");
    expect(["Lead öffnen", "Weiteren anlegen", "Fertig"].every((label) => button(done, label))).toBe(true);
    await act(async () => button(done, "Lead öffnen").click());
    await tick();
    expect(host.querySelector(".crm-detail h2")!.textContent).toBe("Doppelt GmbH");
    expect(host.querySelector('section[aria-label="Lead angelegt"]')).toBeNull();
  });

  it("prüft Eingaben am Feld, gruppiert das Formular und zeigt die Vorschau", async () => {
    expect(validateLead({ firma: "", name: "", rolle: "", telefon: "12", email: "kaputt", website: "nicht gut", notiz: "" })).toEqual({
      firma: "Firma oder Ansprechpartner angeben.", telefon: "Mindestens 6 Ziffern.", email: "Adresse wie name@firma.de.", website: "Adresse wie firma.de." });
    expect(validateLead({ firma: "A", name: "", rolle: "", telefon: "", email: "", website: "", notiz: "" })).toEqual({});
    const api = server([]);
    const host = await mount(<CrmPanel canEdit auswertung={<div />} onOpenApprovals={vi.fn()} />);
    await tick();
    await act(async () => button(host, "Lead anlegen").click());
    const form = host.querySelector<HTMLFormElement>('form[aria-label="Neuen Lead anlegen"]')!;
    expect([...form.querySelectorAll("legend")].map((l) => l.textContent)).toEqual(["1 Wer", "2 Erreichbar", "3 Merken"]);
    const input = (label: string) => [...form.querySelectorAll("label")].find((l) => l.textContent!.startsWith(label))!.querySelector("input")!;
    const type = async (label: string, value: string) => act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input(label), value);
      input(label).dispatchEvent(new Event("input", { bubbles: true }));
    });
    await type("Firma", "Nordlicht GmbH"); await type("E-Mail", "kaputt");
    expect(form.querySelector(".nl-preview strong")!.textContent).toBe("Nordlicht GmbH"); // Vorschau folgt der Eingabe
    expect(form.querySelector(".nl-meter")!.getAttribute("aria-label")).toBe("2 von 7 Angaben ausgefüllt");
    await act(async () => { form.requestSubmit(); await new Promise((r) => setTimeout(r, 0)); });
    const mail = input("E-Mail");
    expect(mail.getAttribute("aria-invalid")).toBe("true");
    expect(form.querySelector(`#${mail.getAttribute("aria-describedby")}`)!.textContent).toBe("Adresse wie name@firma.de.");
    expect(form.querySelector("[role=alert]")!.textContent).toContain("markierten Felder");
    expect(api.calls.some((c) => c.method === "POST")).toBe(false); // nichts gesendet, solange ein Feld falsch ist
    await type("E-Mail", "info@nordlicht.example");
    expect(input("E-Mail").getAttribute("aria-invalid")).toBeNull();
  });

  it("blättert über alle Leads: Seiten, Gesamtzahl und Pfeile über Seitengrenzen", async () => {
    const many = Array.from({ length: 120 }, (_, i) => lead(`L-${i + 1}`, `Firma ${String(i + 1).padStart(3, "0")}`));
    const api = server(many);
    const host = await mount(<CrmPanel canEdit auswertung={<div />} onOpenApprovals={vi.fn()} />);
    await tick();
    const names = () => [...host.querySelectorAll(".crm-table tbody strong")].map((s) => s.textContent);
    const pager = () => host.querySelector(".crm-pager")!;
    expect(names()).toHaveLength(PAGE_SIZE);
    expect(pager().querySelector("[role=status]")!.textContent).toBe("1–50 von 120");
    expect(button(host.querySelector(".crm-filter")!, "Alle").textContent).toContain("120"); // Zahlen über den ganzen Bestand
    expect(button(pager(), "Zurück").disabled).toBe(true);
    // Letzter Lead der ersten Seite -> Pfeil rechts lädt Seite 2 und wählt deren ersten Lead
    await act(async () => [...host.querySelectorAll<HTMLButtonElement>(".crm-table .crm-open")].at(-1)!.click());
    const detail = () => host.querySelector(".crm-detail")!;
    expect(detail().querySelector("h2")!.textContent).toBe("Firma 050");
    expect(detail().querySelector(".crm-pos")!.textContent).toBe("50/120");
    await key("ArrowRight"); await tick();
    expect(detail().querySelector("h2")!.textContent).toBe("Firma 051");
    expect(detail().querySelector(".crm-pos")!.textContent).toBe("51/120");
    expect(pager().querySelector("[role=status]")!.textContent).toBe("51–100 von 120");
    await key("ArrowLeft"); await tick(); // zurück über die Seitengrenze: letzter Lead von Seite 1
    expect(detail().querySelector("h2")!.textContent).toBe("Firma 050");
    expect(names()[0]).toBe("Firma 001");
    await key("ArrowLeft"); expect(detail().querySelector("h2")!.textContent).toBe("Firma 049");
    // Vom allerersten Lead rückwärts: letzte Seite, letzter Lead (120/120); von dort vorwärts wieder zum ersten
    await act(async () => host.querySelector<HTMLButtonElement>(".crm-table .crm-open")!.click());
    await key("ArrowLeft"); await tick();
    expect(detail().querySelector("h2")!.textContent).toBe("Firma 120");
    expect(detail().querySelector(".crm-pos")!.textContent).toBe("120/120");
    expect(pager().querySelector("[role=status]")!.textContent).toBe("101–120 von 120");
    expect(button(pager(), "Weiter").disabled).toBe(true);
    await key("ArrowRight"); await tick();
    expect(detail().querySelector("h2")!.textContent).toBe("Firma 001");
    // Seitenknöpfe; alle Seitenaufrufe nutzen denselben Schnappschuss (stabile Reihenfolge)
    await act(async () => button(pager(), "Weiter").click()); await tick();
    expect(names()[0]).toBe("Firma 051");
    const pagesCalled = api.calls.filter((c) => c.url.startsWith("/api/leads?")).map((c) => new URL(c.url, "http://x").searchParams.get("snapshot"));
    expect(new Set(pagesCalled.slice(1))).toEqual(new Set(["S-1"]));
    // Suche läuft über den ganzen Bestand: ein Lead von der letzten Seite wird gefunden
    const search = host.querySelector<HTMLInputElement>(".crm-filter .cc-search")!;
    await act(async () => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(search, "Firma 117");
      search.dispatchEvent(new Event("input", { bubbles: true }));
      await new Promise((r) => setTimeout(r, 320));
    });
    await tick();
    expect(names()).toEqual(["Firma 117"]);
    expect(host.querySelector(".crm-pager")).toBeNull();
  });

  it("Call-Session läuft am Seitenende auf der nächsten Seite weiter", async () => {
    server(Array.from({ length: PAGE_SIZE + 2 }, (_, i) => lead(`L-${i + 1}`, `Firma ${String(i + 1).padStart(3, "0")}`)));
    const host = await mount(<CrmPanel canEdit auswertung={<div />} onOpenApprovals={vi.fn()} />);
    await tick();
    await act(async () => button(host, "Aufgaben starten").click()); await tick();
    await act(async () => button(host.querySelector(".crm-start")!, "Session starten").click()); await tick();
    for (let i = 0; i < PAGE_SIZE - 1; i++) await key("ArrowRight");
    expect(host.querySelector(".crm-detail h2")!.textContent).toBe("Firma 050");
    expect(host.querySelector(".crm-meter-next")!.textContent).toBe("Danach: nächste Seite");
    await key("1"); await tick(); await tick();
    expect(host.querySelector(".crm-detail h2")!.textContent).toBe("Firma 051");
    expect(host.querySelector(".crm-pos")!.textContent).toBe("51/52");
    await act(async () => button(host.querySelector(".crm-meter")!, "Session beenden").click());
  });

  it("sendet den Besitzernachweis nur bei Lead-Schreibzugriffen und CSV als Rohdatei bis 1 MB", async () => {
    setOwnerProof("room-test1", "ticket-abc");
    const api = server([lead("L-1", "Alpha AG")]);
    const host = await mount(<CrmPanel canEdit auswertung={<div />} onOpenApprovals={vi.fn()} focusLead="L-1" />);
    await tick();
    await act(async () => button(host.querySelector(".crm-detail")!, "Mailbox").click()); await tick();
    const write = api.calls.find((c) => c.url === "/api/leads/L-1/ergebnis")!;
    expect(write.headers).toMatchObject({ "X-Jarvis": "1", "X-Jarvis-Room": "room-test1", "X-Jarvis-Owner": "ticket-abc" });
    expect(api.calls.filter((c) => c.method === "GET").every((c) => !("X-Jarvis-Owner" in c.headers))).toBe(true);
    await act(async () => button(host, "CSV importieren").click());
    const panel = host.querySelector('section[aria-label="Leads aus CSV importieren"]')!;
    expect(panel.querySelector(".crm-limits")!.textContent).toContain("bis 1 MB");
    expect(panel.querySelector(".crm-limits")!.textContent).toContain("bis 2000 Zeilen");
    const choose = async (file: File) => {
      const input = panel.querySelector<HTMLInputElement>('input[type="file"]')!;
      Object.defineProperty(input, "files", { configurable: true, value: [file] });
      await act(async () => { input.dispatchEvent(new Event("change", { bubbles: true })); await new Promise((r) => setTimeout(r, 0)); });
    };
    await choose(new File([new Uint8Array(1_000_001)], "zu-gross.csv", { type: "text/csv" }));
    expect(panel.querySelector("[role=alert]")!.textContent).toContain("höchstens 1 MB");
    expect(api.calls.some((c) => c.url.startsWith("/api/leads/import"))).toBe(false);
    const ok = new File([new Uint8Array(1_000_000)], "genau.csv", { type: "text/csv" }); // genau 1 MB: weit über den alten 200 KB
    await choose(ok);
    const check = api.calls.find((c) => c.url === "/api/leads/import?ausfuehren=false")!;
    expect(check.body).toBe(ok); // die Datei selbst, kein JSON
    expect(check.headers).toMatchObject({ "X-Jarvis-Owner": "ticket-abc", "Content-Type": "text/csv; charset=utf-8" });
    await act(async () => button(panel, "3 Leads importieren").click()); await tick();
    expect(api.calls.some((c) => c.url === "/api/leads/import?ausfuehren=true")).toBe(true);
    expect(host.querySelector(".crm-ok")!.textContent).toContain("3 Leads importiert");
    setOwnerProof("room-test1", null);
  });

  it("zeigt leeren Zustand, Fehlerzustand und sperrt Beobachter", async () => {
    server([]);
    let host = await mount(<CrmPanel canEdit auswertung={<div />} onOpenApprovals={vi.fn()} />);
    await tick();
    expect(host.querySelector(".crm-empty")!.textContent).toContain("Noch keine Leads");
    act(() => root?.unmount()); document.body.innerHTML = "";
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 404, json: async () => ({}) }) as Response));
    host = await mount(<CrmPanel canEdit auswertung={<div />} onOpenApprovals={vi.fn()} />);
    await tick();
    expect(host.querySelector("[role=alert]")!.textContent).toContain("Jarvis neu starten");
    act(() => root?.unmount()); document.body.innerHTML = "";
    server([lead("L-1", "Alpha AG")]);
    host = await mount(<CrmPanel canEdit={false} auswertung={<div />} onOpenApprovals={vi.fn()} focusLead="L-1" />);
    await tick();
    expect(host.querySelector(".crm-outcomes")).toBeNull();
    expect(host.querySelector(".crm-detail")!.textContent).toContain("Nur Ansicht");
  });
});

const job = (id: string, status: string, extra: Partial<AgentJob> = {}): AgentJob => ({
  id, titel: `Job ${id}`, art: "hintergrund", status, agent: { id: "a", name: "Hintergrund-Agent", art: "Hintergrundauftrag" }, unteragenten: [],
  gespraech: "bg", start: 1000, ende: status === "RUNNING" ? null : 1010, letzte_aktivitaet: 1005, schritte: [], schritte_gesamt: 0, schritte_fertig: 0,
  fortschritt: "", werkzeuge: [], ergebnis: null, fehler: null, unterbrochen_durch_neustart: false, freigaben: [], abbrechbar: status === "RUNNING",
  wiederholbar: status === "FAILED", faehigkeit: null, modell: null, nutzung: null, repo: null, ...extra,
});

describe("Agenten / Jobs", () => {
  it("trennt Gespräche von Jobs und lässt Unbekanntes sichtbar", () => {
    expect(isJob({ art: "vordergrund" })).toBe(false);
    expect(isJob({ art: "echtzeit" })).toBe(false);
    expect(isJob({ art: "hintergrund" })).toBe(true);
    expect(isJob({ art: "etwas_neues" })).toBe(true);
    expect(isJob({ art: "vordergrund", klasse: "job" })).toBe(true); // der Server entscheidet, wenn er es sagt
    expect(isImportant(job("x", "COMPLETED"))).toBe(false);
    expect(isImportant(job("x", "FAILED"))).toBe(true);
  });

  it("zeigt standardmäßig nur wichtige Jobs mit Status und nächster Aktion, keine Chats", async () => {
    const data: AgentCenterData = { stand: 0, hinweis: "Hinweis.", zahlen: {}, agenten: [], rollen: [], jobs: [
      job("laeuft", "RUNNING"), job("wartet", "WAITING_FOR_APPROVAL"), job("kaputt", "FAILED", { fehler: "Kalender nicht erreichbar" }),
      job("fertig", "COMPLETED"), job("chat", "COMPLETED", { art: "vordergrund", klasse: "gespraech", titel: "Wie geht es dir?" }),
      job("live", "RUNNING", { art: "echtzeit", klasse: "gespraech", titel: "Echtzeit-Gespräch" }),
    ] };
    const onApprovals = vi.fn(), onRetry = vi.fn(), onCancel = vi.fn();
    const host = await mount(<AgentCenter data={data} error={null} canAct focusJob={null} busyId={null} onCancel={onCancel} onRetry={onRetry}
      onApprovals={onApprovals} onOpenOrder={vi.fn()} onStartRole={vi.fn()} onRefresh={vi.fn()} />);
    const titles = [...host.querySelectorAll(".ac-job .title")].map((t) => t.textContent);
    expect(titles).toEqual(["Job wartet", "Job kaputt", "Job laeuft"]); // „Braucht dich“ steht vor „Läuft“
    expect([...host.querySelectorAll(".ac-group h4")].map((h) => h.textContent)).toEqual(["Braucht dich2", "Läuft1"]);
    expect(host.textContent).not.toContain("Wie geht es dir?");
    expect(host.textContent).not.toContain("Echtzeit-Gespräch");
    expect(host.textContent).toContain("2 Gesprächszüge sind hier ausgeblendet");
    const rows = [...host.querySelectorAll(".ac-job-row")];
    expect(rows[2].querySelector(".cc-state")!.textContent).toBe("Läuft");
    expect(rows.map((r) => r.querySelector(".ac-who")!.textContent)).toEqual(Array(3).fill("Hintergrund-Agent")); // Rolle je Job
    await act(async () => button(rows[2], "Abbrechen").click());
    await act(async () => button(rows[0], "Freigabe prüfen").click());
    await act(async () => button(rows[1], "Erneut starten").click());
    expect(onCancel).toHaveBeenCalledWith("laeuft");
    expect(onApprovals).toHaveBeenCalled();
    expect(onRetry).toHaveBeenCalledWith("kaputt");
    await act(async () => button(host.querySelector(".ac-summary")!, "4").click()); // „Alle Jobs“: 4, Chats zählen nie mit
    expect(host.querySelectorAll(".ac-job")).toHaveLength(4);
    expect(host.querySelector(".ac-job-row.COMPLETED .ac-none")!.textContent).toBe("nur ansehen"); // keine Aktion heißt das auch
  });

  it("zeigt Rollen als Zeilen mit Einrichtung, Auslastung und nächster Aktion", async () => {
    const role = (id: string, extra: object = {}) => ({ id, name: `Rolle ${id}`, aufgabe: "Lange Beschreibung der Aufgabe.", skill: null, werkzeuge: [], fehlende_werkzeuge: [],
      status: "bereit", grund: "", umsetzung: "Orchestrator + Skill", startbar: true, vorlage: "Vorlage für den Auftrag", jobs_aktiv: 0, jobs_gesamt: 0, letzter_job: null, ...extra });
    const data = { stand: 0, hinweis: "", zahlen: {}, jobs: [job("laeuft", "RUNNING")],
      agenten: [{ id: "k", name: "Kern", rolle: "Hauptagent", modell: "claude-test", status: "bereit", hinweis: "" }],
      rollen: [role("a", { jobs_aktiv: 2 }), role("b", { letzter_job: { id: "laeuft", titel: "x", status: "COMPLETED", start: 1000 } }),
        role("c", { status: "fehlt", grund: "Werkzeug fehlt", startbar: false })] } as unknown as AgentCenterData;
    const onStartRole = vi.fn(async () => undefined);
    const host = await mount(<AgentCenter data={data} error={null} canAct focusJob={null} busyId={null} onCancel={vi.fn()} onRetry={vi.fn()}
      onApprovals={vi.fn()} onOpenOrder={vi.fn()} onStartRole={onStartRole} onRefresh={vi.fn()} />);
    const roles = [...host.querySelectorAll(".ac-roles .ac-role")];
    expect(host.querySelector(".ac-roles header .cc-tag")!.textContent!.trim()).toBe("1 von 3 aktiv");
    expect(roles.map((r) => r.querySelector(".ac-role-load")!.textContent)).toEqual(["2 aktiv", expect.stringContaining("zuletzt"), "frei"]);
    expect(roles.map((r) => r.querySelector(".cc-state")!.textContent)).toEqual(["Bereit", "Bereit", "Fehlt"]);
    expect(roles[0].textContent).not.toContain("Lange Beschreibung"); // Beschreibung erst nach Aufklappen
    expect(roles[2].textContent).toContain("Werkzeug fehlt");          // der Grund einer Störung bleibt sichtbar
    expect(button(roles[2], "Auftrag starten")).toBeUndefined();
    const toggle = roles[0].querySelector<HTMLButtonElement>(".ac-role-name")!;
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    await act(async () => toggle.click());
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(roles[0].textContent).toContain("Lange Beschreibung");
    await act(async () => button(roles[1], "Auftrag starten").click());
    await act(async () => { button(roles[1], "Als Job starten").click(); await new Promise((r) => setTimeout(r, 0)); });
    expect(onStartRole).toHaveBeenCalledWith(expect.objectContaining({ id: "b" }), "Vorlage für den Auftrag");
    expect(host.querySelector<HTMLDetailsElement>(".ac-cores")!.open).toBe(false); // Modelle und Kerne eingeklappt
  });
});

describe("Skills und Automationen", () => {
  it("zeigt nur aktive Skills und laufende oder anstehende Automationen", async () => {
    const skill = (id: string, aktiviert: boolean) => ({ id, name: id, beschreibung: "Beschreibung", version: "1", herkunft: "mitgeliefert", quelle: "", commit: "",
      art: "skill", werkzeuge: [], fehlende_werkzeuge: [], rahmen: "frei", berechtigungen: {}, bestaetigung: "", zeitlimit_s: 60, abbrechbar: true, aktiviert, sperrgrund: "", verdachtsstellen: 0 });
    const run = (id: string, status: string) => ({ id, faehigkeit: "github_analysieren", quelle: "dashboard", status, start: 1, ende: null, eingabe: {}, ergebnis: null, fehler: null, abbrechbar: true });
    const flow = (id: string, aktiv: boolean) => ({ id, name: id, beschreibung: "", aktiv, art: "Hintergrund-Agent", zeitplan: "werktags 07:30", letzter_lauf: null });
    const onRunAction = vi.fn();
    const host = await mount(<SkillsOverview canEdit busy={null} platformError={null} automationError={null} onStartSkill={vi.fn()} onToggleSkill={vi.fn()}
      onRunAction={onRunAction} onOpenApprovals={vi.fn()} onOpenJobs={vi.fn()}
      platform={{ faehigkeiten: [{ name: "github_analysieren", titel: "GitHub analysieren", beschreibung: "", berechtigung: "lesen", berechtigung_text: "nur lesen",
        abbrechbar: true, eingabe: { properties: {}, required: [] }, aktiviert: true }], skills: [skill("aktiv-skill", true), skill("aus-skill", false)] as any,
        laeufe: [run("r1", "RUNNING"), run("r2", "COMPLETED"), run("r3", "FAILED"), run("r4", "CANCELLED")] as any,
        berechtigungen: {}, freigabepflichtig: [], github: { git: true, token: false, freigegeben: [], privat_moeglich: false }, cloud: { erlaubt: [], verfuegbar: [], sperrgrund: "", anbieter: "" } }}
      automations={{ stand: 0, agenten: [], ablaeufe: [flow("Morgenbriefing", true), flow("Alt-Ablauf", false)], ablaeufe_schleife: "läuft",
        n8n: { status: "nicht_eingerichtet", hinweis: "", workflows: [] }, integrationen: [], jobs_laufend: 0 }} />);
    const sections = host.querySelectorAll(".so > section");
    const visible = (el: Element) => [...el.querySelectorAll(":scope > .so-list > .so-row strong")].map((s) => s.textContent);
    expect(visible(sections[0])).toEqual(["aktiv-skill", "GitHub analysieren"]);
    expect(sections[0].querySelector("details summary")!.textContent).toContain("1 registrierte, aber nicht aktive");
    expect(visible(sections[1])).toEqual(["GitHub analysieren", "Morgenbriefing"]); // r2–r4 und der abgeschaltete Ablauf fehlen
    expect(sections[1].textContent).toContain("1 abgeschaltete Abläufe sind hier ausgeblendet");
    expect(sections[1].querySelector(".so-row .cc-state")!.textContent).toBe("Läuft");
    await act(async () => button(sections[1], "Abbrechen").click());
    expect(onRunAction).toHaveBeenCalledWith("r1", "abbrechen");
  });
});

describe("Designsystem „Lime auf Tinte“", () => {
  const css = readFileSync("src/command/command.css", "utf8") + readFileSync("src/command/window.css", "utf8");
  const token = (name: string, scope = ".cc, .lt {") => {
    const block = css.slice(css.indexOf(scope));
    return new RegExp(`${name}: (#[0-9A-Fa-f]{6})`).exec(block)![1];
  };
  const lum = (hex: string) => {
    const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
    return 0.2126 * r + 0.7152 * g + 0.0722 * b;
  };
  const ratio = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

  it("hält Text-Kontraste über 4,5:1 (hell und dunkle Bühne)", () => {
    const pairs: [string, string, string][] = [
      ["Text auf Grund", token("--cc-text"), token("--cc-bg")], ["Gedämpft auf Grund", token("--cc-muted"), token("--cc-bg")],
      ["Gedämpft auf getönter Fläche", token("--cc-muted"), token("--cc-panel-2")], ["Text auf Chip", token("--cc-text"), token("--cc-chip")],
      ["Tinte auf Lime", token("--cc-on-brand"), token("--cc-brand")], ["Weiß auf Tinte", "#FFFFFF", token("--cc-navy")],
      ["Schiene: Text", token("--cc-on-ink"), token("--cc-navy")], ["Schiene: gedämpft", token("--cc-on-ink-muted"), token("--cc-navy")],
      ["Braucht dich", token("--cc-you"), token("--cc-you-soft")], ["Erledigt", token("--cc-ok"), token("--cc-ok-soft")],
      ["Hinweis", token("--cc-warn"), token("--cc-warn-soft")], ["Fehler", token("--cc-danger"), token("--cc-danger-soft")],
      ["Link auf Grund", token("--cc-blue-text"), token("--cc-bg")],
      ["Bühne: Text", token("--cc-text", ".cc.cw {"), token("--cc-bg", ".cc.cw {")], ["Bühne: gedämpft", token("--cc-muted", ".cc.cw {"), token("--cc-bg", ".cc.cw {")],
      ["Bühne: gedämpft auf Fläche", token("--cc-muted", ".cc.cw {"), token("--cc-panel", ".cc.cw {")], ["Bühne: Tinte auf Lime", token("--cc-on-navy", ".cc.cw {"), token("--cc-navy", ".cc.cw {")],
      ["Bühne: Fehlertext", token("--cc-danger", ".cc.cw {"), token("--cc-danger-soft", ".cc.cw {")],
    ];
    for (const [name, fg, bg] of pairs) expect(ratio(fg, bg), `${name}: ${fg} auf ${bg}`).toBeGreaterThanOrEqual(4.5);
    // Feldränder und Fokus: mindestens 3:1 (WCAG 1.4.11)
    expect(ratio(token("--cc-field"), token("--cc-bg"))).toBeGreaterThanOrEqual(3);
    expect(ratio(token("--cc-field"), token("--cc-input"))).toBeGreaterThanOrEqual(3);
  });

  it("nutzt keine weißen Flächen mehr und respektiert reduzierte Bewegung", () => {
    expect(css).not.toMatch(/background(-color)?: #fff\b/i);
    expect(css).not.toMatch(/background(-color)?: #FFFFFF\b/);
    expect(css).toContain("prefers-reduced-motion: reduce");
    expect(readFileSync("src/command/window.css", "utf8")).toMatch(/prefers-reduced-motion: reduce\) \{\s*\.orb-ring, \.cw-map \{ animation: none/);
  });
});
