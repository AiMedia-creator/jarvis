import type { ReactNode } from "react";

/** Einheitlicher Rahmen der „Mehr“-Ansichten (dunkle Bühne in der gemeinsamen Schiene) mit sichtbarem Rückweg. */
export function ViewFrame({
  eyebrow,
  title,
  meta,
  onBack,
  children,
  className = "",
}: {
  eyebrow: string;
  title: ReactNode;
  meta?: ReactNode;
  onBack: () => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`view-frame ${className}`} aria-label={eyebrow}>
      <header className="view-head">
        <div>
          <div className="view-eyebrow">{eyebrow}</div>
          <h2 className="view-title">{title}</h2>
          {meta && <div className="view-meta">{meta}</div>}
        </div>
        <button
          type="button"
          className="view-close"
          onClick={onBack}
          aria-label="Ansicht schließen, zurück zur Übersicht"
          title="Ansicht schließen"
        >
          <span aria-hidden="true">×</span>
        </button>
      </header>
      <div className="view-body">{children}</div>
    </section>
  );
}

export function relativeTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "Zeit unbekannt";
  const time = new Date(iso).getTime();
  if (Number.isNaN(time)) return "Zeit unbekannt";
  const minutes = Math.round((now - time) / 60000);
  if (minutes < 1) return "gerade eben";
  if (minutes < 60) return `vor ${minutes} Min.`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `vor ${hours} Std.`;
  return new Date(iso).toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit" });
}
