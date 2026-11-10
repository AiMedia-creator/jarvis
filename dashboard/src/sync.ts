import type { ContentView } from "./view/intent";

export type UiSyncState = {
  view?: { city_id?: string; lat: number; lon: number; height: number };
  cameraId?: string | null;
  /** Sichtbare Inhaltsansicht (nur UI-Zustand; Audio und Tokens werden nie gespiegelt). */
  content?: { view: ContentView; query?: string };
};
type Message = { source: string; revision: number; state: UiSyncState };

export class UiSync {
  private readonly source = crypto.randomUUID();
  private revision = 0;
  private readonly channel: BroadcastChannel | null =
    typeof BroadcastChannel !== "undefined"
      ? new BroadcastChannel("jarvis-ui-sync")
      : null;
  private readonly storageKey = "jarvis-ui-sync-v1";
  private readonly listeners = new Set<(state: UiSyncState) => void>();
  private readonly onMessage = (event: MessageEvent<Message>) =>
    this.accept(event.data);
  private readonly onStorage = (event: StorageEvent) => {
    if (event.key === this.storageKey && event.newValue) {
      try {
        this.accept(JSON.parse(event.newValue) as Message);
      } catch {}
    }
  };
  constructor() {
    this.channel?.addEventListener("message", this.onMessage);
    window.addEventListener("storage", this.onStorage);
  }
  subscribe(listener: (state: UiSyncState) => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }
  publish(state: UiSyncState): void {
    const message: Message = {
      source: this.source,
      revision: ++this.revision,
      state,
    };
    this.channel?.postMessage(message);
    try {
      localStorage.setItem(this.storageKey, JSON.stringify(message));
    } catch {}
  }
  private accept(message: Message): void {
    if (
      !message ||
      message.source === this.source ||
      !Number.isInteger(message.revision) ||
      message.revision <= this.revision
    )
      return;
    this.revision = message.revision;
    for (const listener of this.listeners) listener(message.state);
  }
  close(): void {
    this.channel?.removeEventListener("message", this.onMessage);
    window.removeEventListener("storage", this.onStorage);
    this.channel?.close();
    this.listeners.clear();
  }
}
