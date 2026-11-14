import { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { api, type AgentCenterData, type Approval, type Automations, type Platform, type Settings } from "../command/api";
import { CommandCenter, type CommandCenterProps } from "../command/CommandCenter";
import { isJob } from "../command/AgentCenter";
import { LiveCamera, useBusEvents, type CameraInfo } from "../command/LiveCamera";
import { Icon } from "../command/icons";
import "../command/command.css";
import "../command/window.css";

const MapboxWorld = lazy(() => import("../world/MapboxWorld"));

export type CommandWindowProps = Omit<CommandCenterProps, "automations" | "approvals" | "onOpenApprovals" | "settings" | "onSettings" | "platform"
  | "jobs" | "jobsError" | "busyJob" | "onOpenJob" | "onCancelJob" | "stage" | "understood" | "onOpenMemory"> & {
  /** Ort der Kartenansicht („Zeig Berlin“), null = keine Karte. */
  mapQuery: string | null;
  onCloseMap: () => void;
  /** Öffnet einen Abschnitt im Dashboard-Fenster (Freigaben, Jobs, Gedächtnis …). */
  onDashboard: (section: string) => void;
  /** Hinweis, dass ein Befehl ins Dashboard weitergereicht wurde. */
  forwarded: string | null;
  demo?: boolean;
};

/**
 * Command Center als eigenes Fenster: kreisförmiger Sprachassistent, Gespräch, Texteingabe, wartende Freigaben und
 * laufende Jobs. Eigener Raum, eigene Steuerung – unabhängig vom Dashboard-Fenster.
 */
export default function CommandWindow({ mapQuery, onCloseMap, onDashboard, forwarded, demo, ...center }: CommandWindowProps) {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [automations, setAutomations] = useState<Automations | null>(null);
  const [agents, setAgents] = useState<AgentCenterData | null>(null);
  const [agentError, setAgentError] = useState<string | null>(null);
  const [settings, setSettings] = useState<Settings | null>(null);
  const [platform, setPlatform] = useState<Platform | null>(null);
  const [camera, setCamera] = useState<CameraInfo | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const alive = useRef(true);
  useEffect(() => { document.title = "Jarvis Command Center"; return () => void (alive.current = false); }, []);

  const loadApprovals = useCallback(async () => {
    try { const d = await api.approvals(); if (alive.current && Array.isArray(d?.freigaben)) setApprovals(d.freigaben); } catch { /* letzter Stand bleibt */ }
  }, []);
  const loadWork = useCallback(async () => {
    try { const a = await api.automations(); if (alive.current && Array.isArray(a?.agenten)) setAutomations(a); } catch { /* Diagnose zeigt dann „–“ */ }
    try {
      const d = await api.agentCenter();
      if (!alive.current) return;
      if (!Array.isArray(d?.jobs)) throw new Error("Unerwartete Antwort");
      setAgents(d); setAgentError(null);
    } catch { if (alive.current) setAgentError("Jobs nicht erreichbar – läuft der Jarvis-Server?"); }
  }, []);
  useEffect(() => {
    void loadApprovals(); void loadWork();
    api.settings().then((d) => { if (alive.current && Array.isArray(d?.stimmen)) setSettings(d); }).catch(() => undefined);
    api.capabilities().then((d) => { if (alive.current && Array.isArray(d?.faehigkeiten)) setPlatform({ ...d, laeufe: d.laeufe ?? [], skills: d.skills ?? [] }); }).catch(() => undefined);
    const fast = window.setInterval(() => void loadApprovals(), 10_000);
    const slow = window.setInterval(() => void loadWork(), 30_000);
    return () => { window.clearInterval(fast); window.clearInterval(slow); };
  }, [loadApprovals, loadWork]);
  const timer = useRef<number | undefined>(undefined);
  useBusEvents(center.connected, (ev) => {
    if (ev.typ === "kamera") setCamera(ev.aktion === "schliessen" ? null : (ev.kamera ?? null));
    else { window.clearTimeout(timer.current); timer.current = window.setTimeout(() => { void loadWork(); void loadApprovals(); }, 300); }
  });
  const cancelJob = useCallback(async (id: string) => {
    setBusy(id);
    try { const r = await api.cancelJob(id); if (!r.abgebrochen) setNotice("Der Job war bereits beendet."); }
    catch (e) { setNotice(`Abbrechen nicht möglich: ${e instanceof Error ? e.message : "unbekannt"}`); }
    finally { setBusy(null); void loadWork(); }
  }, [loadWork]);
  const dashboard = (section: string, label: string) => { onDashboard(section); setNotice(`${label} ist im Dashboard-Fenster geöffnet.`); };

  // Esc schließt die Karte, wenn gerade nichts gesprochen oder geschrieben wird.
  useEffect(() => {
    if (mapQuery == null) return;
    const onKey = (event: KeyboardEvent) => {
      const tag = (event.target as HTMLElement | null)?.tagName;
      if (event.key === "Escape" && tag !== "INPUT" && tag !== "TEXTAREA") onCloseMap();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mapQuery, onCloseMap]);

  const place = mapQuery ? mapQuery.replace(/^\p{Ll}/u, (c) => c.toUpperCase()) : null;
  return (
    <section className={`cc cw ${mapQuery != null ? "with-map" : ""}`} aria-label="Jarvis Command Center">
      <header className="cw-top">
        <div className="cc-brand"><span className="cc-logo" aria-hidden="true" /><span>Jarvis</span></div>
        <h1>Command Center</h1>
        <div className="cw-top-actions">
          {demo && <span className="cc-tag demo">Demo</span>}
          <span className={`cc-state ${center.connected ? "bereit" : "fehler"}`} role="status">{center.connected ? "Verbunden" : "Getrennt"}</span>
          <button type="button" className="cc-btn small" onClick={() => dashboard("uebersicht", "Die Übersicht")}><Icon name="external" /> Dashboard</button>
        </div>
      </header>
      {(notice || forwarded) && (
        <p className="cw-notice" role="status">{notice ?? forwarded} {notice && <button type="button" className="cc-link" onClick={() => setNotice(null)}>OK</button>}</p>
      )}
      <div className="cw-body">
        <div className="cw-center">
          <CommandCenter {...center} stage understood={place ? `Karte öffnen · ${place}` : forwarded}
            automations={automations} approvals={approvals} onOpenApprovals={() => dashboard("freigaben", "Die Freigaben")}
            settings={settings} onSettings={setSettings} platform={platform}
            jobs={agents ? agents.jobs.filter(isJob) : null} jobsError={agentError} busyJob={busy}
            onOpenJob={() => dashboard("agenten", "Agenten / Jobs")} onCancelJob={(id) => void cancelJob(id)}
            onOpenMemory={() => dashboard("gedaechtnis", "Das Gedächtnis")} />
        </div>
        {mapQuery != null && (
          <aside className="cw-map" aria-label={`Karte: ${place || "Weltansicht"}`}>
            <header><div><h2>{place || "Weltansicht"}</h2><p className="cw-map-src">Karte aus Archivaufnahmen, kein Livebild. Quellen stehen unten rechts in der Karte.</p></div>
              <button type="button" className="cc-btn small" onClick={onCloseMap}>Karte schließen <kbd>Esc</kbd></button></header>
            <div className="cw-map-body">
              <Suspense fallback={<p className="cc-empty" style={{ padding: 24 }}>Karte lädt …</p>}>
                <MapboxWorld query={mapQuery || undefined} webcams={null} directFallback />
              </Suspense>
            </div>
          </aside>
        )}
      </div>
      {camera && <LiveCamera camera={camera} onClose={() => setCamera(null)} />}
    </section>
  );
}
