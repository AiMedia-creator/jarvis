/**
 * Zentrale Ansichtssteuerung (reiner Reducer, ohne Seiteneffekte).
 *
 * Stärke einer Entscheidung: Werkzeug-Hinweis 1 < thematische Frage 2 < expliziter Befehl / Klick 3.
 * Ein neuer Hinweis gewinnt nur, wenn er mindestens so stark ist wie die aktuelle Ansicht.
 * Ereignisse eines abgebrochenen oder älteren Sprachzugs werden verworfen.
 * Ein Ansichtswechsel löst nie eine KI-Anfrage aus und berührt keine Audio-Laufzeit.
 */
import type { ContentView, Intent, OpenableView } from "./intent";

export type Strength = 0 | 1 | 2 | 3;
export type ViewModel = {
  view: ContentView;
  query?: string;
  strength: Strength;
  /** Wer die aktuelle Ansicht geöffnet hat. */
  origin: "start" | "tool" | "voice" | "manual" | "sync";
  /** Aktueller Sprachzug (stream_id); nur seine Hinweise zählen. */
  stream: string | null;
  /** Bekannte Züge in Reihenfolge (begrenzt), um ältere zu erkennen. */
  streams: string[];
  revoked: string[];
  /** Erhöht sich bei jedem echten Wechsel (für Animationen und Sync). */
  revision: number;
};

export type ViewAction =
  | { type: "turn"; stream: string }
  | { type: "stopped"; stream: string | null }
  | { type: "utterance"; stream: string | null; intent: Intent }
  | { type: "tool"; stream: string | null; name: string }
  | { type: "manual"; view: ContentView; query?: string }
  | { type: "back" }
  | { type: "sync"; view: ContentView; query?: string };

export const initialView: ViewModel = {
  view: "conversation",
  strength: 0,
  origin: "start",
  stream: null,
  streams: [],
  revoked: [],
  revision: 0,
};

/** Werkzeuge, deren Aufruf eine Ansicht nahelegt (schwacher Hinweis). */
const TOOL_HINTS: Record<string, OpenableView> = {
  nachrichten: "news",
  telegram_neuigkeiten: "news",
};
const KEEP_STREAMS = 32;

function remember(state: ViewModel, stream: string): ViewModel {
  if (state.streams.includes(stream)) return { ...state, stream };
  return {
    ...state,
    stream,
    streams: [...state.streams, stream].slice(-KEEP_STREAMS),
  };
}

/** Gehört das Ereignis zum aktuellen, nicht abgebrochenen Zug? Neue Züge werden dabei übernommen. */
function admit(state: ViewModel, stream: string | null): ViewModel | null {
  if (stream === null) return state; // lokal erzeugt (z. B. ohne Server)
  if (state.revoked.includes(stream)) return null;
  if (stream === state.stream) return state;
  if (state.streams.includes(stream)) return null; // älterer Zug
  return remember(state, stream);
}

function open(
  state: ViewModel,
  view: ContentView,
  strength: Strength,
  origin: ViewModel["origin"],
  query?: string,
): ViewModel {
  if (strength < state.strength && view !== state.view) return state;
  if (view === state.view && query === state.query && strength <= state.strength)
    return state;
  return {
    ...state,
    view,
    query,
    strength: view === "conversation" ? 0 : strength,
    origin,
    revision: state.revision + 1,
  };
}

export function viewReducer(state: ViewModel, action: ViewAction): ViewModel {
  switch (action.type) {
    case "turn": {
      if (state.revoked.includes(action.stream)) return state;
      return remember(state, action.stream);
    }
    case "stopped": {
      const stream = action.stream ?? state.stream;
      if (!stream || state.revoked.includes(stream)) return state;
      return { ...state, revoked: [...state.revoked, stream].slice(-KEEP_STREAMS) };
    }
    case "utterance": {
      const current = admit(state, action.stream);
      if (!current) return state;
      const intent = action.intent;
      if (intent.kind === "back")
        return open(current, "conversation", 3, "voice");
      if (intent.kind !== "open") return current;
      return open(current, intent.view, intent.explicit ? 3 : 2, "voice", intent.query);
    }
    case "tool": {
      const current = admit(state, action.stream);
      if (!current) return state;
      const view = TOOL_HINTS[action.name];
      if (!view || current.view !== "conversation") return current;
      return open(current, view, 1, "tool");
    }
    case "manual":
      return open(state, action.view, 3, "manual", action.query);
    case "back":
      return open(state, "conversation", 3, "manual");
    case "sync":
      // Spiegel eines anderen Fensters: übernimmt die Ansicht, ohne Züge zu verändern.
      return { ...open(state, action.view, 3, "sync", action.query) };
  }
}
