import { useEffect, useState } from "react";
import type { Approval } from "./api";
import { remaining } from "./model";

const RISK: Record<string, string> = {
  mail_senden: "Nachricht an Dritte",
  hubspot_mail_senden: "Nachricht an Dritte",
  termin_erstellen: "Kalender ändern",
  drive_ordnen: "Dateien umbauen",
  n8n_starten: "Workflow nach außen",
};

export function ApprovalsPanel({
  items,
  canDecide,
  onDecide,
  compact = false,
  query = "",
}: {
  items: Approval[];
  canDecide: boolean;
  onDecide: (id: string, action: "freigeben" | "verwerfen") => Promise<void>;
  compact?: boolean;
  query?: string;
}) {
  const [now, setNow] = useState(Date.now());
  const [confirming, setConfirming] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);
  const q = query.trim().toLowerCase();
  const open = items.filter((a) => a.status === "WAITING_FOR_APPROVAL")
    .filter((a) => !q || JSON.stringify(a.vorschau).toLowerCase().includes(q));
  const done = compact ? [] : items.filter((a) => a.status !== "WAITING_FOR_APPROVAL").slice(0, 8);
  const decide = async (id: string, action: "freigeben" | "verwerfen") => {
    setBusy(id);
    try {
      await onDecide(id, action);
    } finally {
      setBusy(null);
      setConfirming(null);
    }
  };
  return (
    <div className={`cc-approvals ${compact ? "compact" : ""}`}>
      {open.length === 0 && <p className="cc-empty">Keine offenen Freigaben.</p>}
      {open.map((a) => (
        <article key={a.id} className="cc-approval" aria-label={`Freigabe ${a.vorschau.titel}`}>
          <header>
            <strong>{a.vorschau.titel}</strong>
            <span className="cc-tag">{a.vorschau.ziel}</span>
            <span className="cc-tag risk">{RISK[a.werkzeug] ?? "Außenwirkung"}</span>
            <span className="cc-timer" aria-label="Ablaufzeit">{remaining(a.laeuft_ab, now)}</span>
          </header>
          {/* Vorschau unverändert aus dem Dispatcher – genau das wird ausgeführt. */}
          <dl>
            {a.vorschau.zeilen.map((row, i) => (
              <div key={i} className={row.wert.length > 120 ? "long" : ""}>
                <dt>{row.feld}</dt>
                <dd>{row.wert}</dd>
              </div>
            ))}
          </dl>
          <footer>
            <span className="cc-status">
              {a.job ? `Job ${a.job.status.toLowerCase()}` : "kein Job"} · {a.graph === "wartet" ? "Graph wartet" : "ohne Graph-Zweig"} · ID {a.id}
            </span>
            {canDecide ? (
              confirming === a.id ? (
                <span className="cc-confirm">
                  <span>Wirklich ausführen?</span>
                  <button type="button" className="cc-btn accent" disabled={busy === a.id} onClick={() => void decide(a.id, "freigeben")}>
                    Ja, ausführen
                  </button>
                  <button type="button" className="cc-btn" onClick={() => setConfirming(null)}>Abbrechen</button>
                </span>
              ) : (
                <span className="cc-confirm">
                  <button type="button" className="cc-btn accent" disabled={busy === a.id} onClick={() => setConfirming(a.id)}>
                    Freigeben …
                  </button>
                  <button type="button" className="cc-btn" disabled={busy === a.id} onClick={() => void decide(a.id, "verwerfen")}>
                    Verwerfen
                  </button>
                </span>
              )
            ) : (
              <span className="cc-status">Nur Ansicht</span>
            )}
          </footer>
        </article>
      ))}
      {done.length > 0 && (
        <details className="cc-history">
          <summary>Zuletzt entschieden ({done.length})</summary>
          <ul>
            {done.map((a) => (
              <li key={a.id}>
                <span>{a.vorschau.titel}</span> <span className="cc-tag">{a.status_text}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
