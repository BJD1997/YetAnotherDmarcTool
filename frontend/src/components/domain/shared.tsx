import type { CheckStatus } from "../../api/dnsChecks";

export function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div className="stat-tile-value num">{value}</div>
      <div className="stat-tile-label">{label}</div>
    </div>
  );
}

// CheckStatus -> dataviz status role. "error" (couldn't determine) reads as
// more ambiguous than "fail" (confirmed non-compliant), so it maps to the
// mid-severity "serious" role rather than the worst one.
const STATUS_TO_ROLE: Record<CheckStatus, "good" | "warning" | "serious" | "critical"> = {
  pass: "good",
  warn: "warning",
  error: "serious",
  fail: "critical",
};

export function StatusBadge({ status }: { status: CheckStatus }) {
  return <span className={`badge badge--${STATUS_TO_ROLE[status]}`}>{status}</span>;
}

/** Zero-dependency stand-in for a per-service/per-provider logo — this repo
 * has no icon library or asset pipeline, so a colored initial letter is used
 * instead of a real brand icon. Colors are the dataviz skill's validated
 * 8-hue categorical sequence (tokens.css --cat-1..8), assigned by a stable
 * hash so the same service always gets the same color. */
function catSlotForLabel(label: string): number {
  let hash = 0;
  for (let i = 0; i < label.length; i++) hash = (hash * 31 + label.charCodeAt(i)) >>> 0;
  return (hash % 8) + 1;
}

export function ServiceBadge({ label }: { label: string }) {
  const initial = label.trim().charAt(0).toUpperCase() || "?";
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: "1.4rem",
        height: "1.4rem",
        borderRadius: "50%",
        background: `var(--cat-${catSlotForLabel(label)})`,
        color: "#fff",
        fontSize: "0.7rem",
        fontWeight: 700,
        marginRight: "0.5rem",
        flexShrink: 0,
      }}
    >
      {initial}
    </span>
  );
}
