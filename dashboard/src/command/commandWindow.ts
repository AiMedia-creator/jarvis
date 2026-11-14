/**
 * Das Command Center läuft in einem eigenen Fenster (?screen=command) mit eigenem Raum und eigener Steuerung.
 * Das Dashboard öffnet es über einen Link und reicht Aufträge über den lokalen Speicher weiter – nie über die Adresse,
 * damit kein Auftragstext in Verlauf oder Protokollen landet.
 */
export const COMMAND_WINDOW = "jarvis-command";
export const COMMAND_URL = "?screen=command";
const PENDING_KEY = "jarvis-command-pending-v1";
const MAX_AGE_MS = 120_000;

type Pending = { id: string; text: string; ts: number };

/** Warteschlange: mehrere Übergaben kurz nacheinander gehen nicht verloren. Alte und kaputte Einträge fallen weg. */
function readPending(raw: string | null): Pending[] {
  try {
    const value = JSON.parse(raw || "[]") as Pending | Pending[] | null;
    const list = Array.isArray(value) ? value : value ? [value] : [];
    return list.filter((p) => p && typeof p.text === "string" && typeof p.ts === "number" && Date.now() - p.ts <= MAX_AGE_MS && p.text.trim()).slice(-10);
  } catch {
    return [];
  }
}

/**
 * Öffnet das Command-Center-Fenster oder holt das vorhandene nach vorn (ohne es neu zu laden).
 * Gibt false zurück, wenn der Browser das Fenster blockiert hat – der Aufrufer zeigt dann den Link.
 */
export function openCommandCenter(text?: string): boolean {
  const value = text?.trim();
  if (value) {
    try {
      const queue = [...readPending(localStorage.getItem(PENDING_KEY)), { id: crypto.randomUUID(), text: value.slice(0, 8000), ts: Date.now() } satisfies Pending];
      localStorage.setItem(PENDING_KEY, JSON.stringify(queue));
    } catch { /* ohne Speicher öffnet sich das Fenster trotzdem, nur ohne Auftrag */ }
  }
  const win = window.open("", COMMAND_WINDOW);
  if (!win) return false;
  try {
    // Neues Fenster ist leer -> Command Center laden. Ein bestehendes behält Gespräch und Mikrofon.
    if (win.location.href === "about:blank" || !win.location.search.includes("screen=command")) win.location.href = COMMAND_URL;
  } catch {
    win.location.href = COMMAND_URL;
  }
  win.focus();
  return true;
}

/** Nimmt alle wartenden Aufträge genau einmal entgegen, in der Reihenfolge der Übergabe (im Command-Center-Fenster). */
export function takePendingCommands(): string[] {
  try {
    const pending = readPending(localStorage.getItem(PENDING_KEY));
    localStorage.removeItem(PENDING_KEY);
    return pending.map((p) => p.text);
  } catch {
    return [];
  }
}
/** Der nächste wartende Auftrag oder null (für einzelne Abfragen und Tests). */
export function takePendingCommand(): string | null {
  const [first, ...rest] = takePendingCommands();
  if (rest.length) {
    try { localStorage.setItem(PENDING_KEY, JSON.stringify(rest.map((text) => ({ id: crypto.randomUUID(), text, ts: Date.now() })))); } catch { /* Rest verfällt */ }
  }
  return first ?? null;
}

/** Meldet Aufträge, die das Dashboard übergibt, während das Command Center schon offen ist. */
export function onPendingCommand(listener: (text: string) => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key !== PENDING_KEY || !event.newValue) return;
    for (const text of takePendingCommands()) listener(text);
  };
  window.addEventListener("storage", onStorage);
  return () => window.removeEventListener("storage", onStorage);
}
