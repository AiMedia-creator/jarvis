import type { ReactNode } from "react";

export type StatusTone = "ok" | "warn" | "alert" | "data" | "off";

/** Leuchtender Zustandsindikator, z. B. ONLINE / SECURE. */
export function StatusTag({
  tone = "ok",
  children,
}: {
  tone?: StatusTone;
  children: ReactNode;
}) {
  return <span className={`j-status ${tone}`}>{children}</span>;
}
