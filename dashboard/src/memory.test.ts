import { describe, expect, it } from "vitest";
import { filterMemoryFacts, isSensitiveMemoryText, normalizeMemorySnapshot } from "./components/MemoryPanel";

describe("memory V3 frontend contract", () => {
  it("normalizes JSON snapshots and bounds every displayed array", () => {
    const payload = { facts: Array.from({ length: 60 }, (_, i) => ({ id: `${i}`, content: `Fact ${i}`, category: "note", updated_at: "now" })), episodes: [{ id: "e1", conversation_id: "haupt", user_text: "hi", assistant_text: "hello", status: "done", created_at: "now" }], stats: { facts: 60, episodes: 1 }, events: [{ id: "x", type: "memory", action: "created" }] };
    const snapshot = normalizeMemorySnapshot(payload);
    expect(snapshot.facts).toHaveLength(50);
    expect(snapshot.episodes[0].conversation_id).toBe("haupt");
    expect(snapshot.stats.facts).toBe(50);
    expect(snapshot.events[0].action).toBe("created");
  });
  it("filters facts as plain text and never needs HTML", () => {
    const facts = normalizeMemorySnapshot({ facts: [{ id: "1", content: "<script>alert(1)</script> project", category: "project" }] }).facts;
    expect(filterMemoryFacts(facts, "PROJECT")).toHaveLength(1);
    expect(facts[0].content).toContain("<script>");
  });
  it("rejects secret-like notes before POST", () => {
    expect(isSensitiveMemoryText("the api key is sk-test123")).toBe(true);
    expect(isSensitiveMemoryText("Jarvis prefers concise status notes")).toBe(false);
  });
});
