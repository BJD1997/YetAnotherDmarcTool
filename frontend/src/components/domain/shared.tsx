import type { CheckStatus } from "../../api/dnsChecks";

export function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div style={{ fontSize: "1.5rem", fontWeight: 600 }}>{value}</div>
      <div style={{ color: "#6b7280", fontSize: "0.85rem" }}>{label}</div>
    </div>
  );
}

export function StatusBadge({ status }: { status: CheckStatus }) {
  const colors: Record<CheckStatus, [string, string]> = {
    pass: ["#dcfce7", "#166534"],
    warn: ["#fef3c7", "#92400e"],
    fail: ["#fee2e2", "#991b1b"],
    error: ["#f3f4f6", "#4b5563"],
  };
  const [background, color] = colors[status];
  return (
    <span
      style={{
        display: "inline-block",
        fontSize: "0.7rem",
        fontWeight: 600,
        textTransform: "uppercase",
        padding: "0.1rem 0.5rem",
        borderRadius: 999,
        background,
        color,
        marginRight: "0.5rem",
      }}
    >
      {status}
    </span>
  );
}

const INITIAL_BADGE_COLORS = ["#2563eb", "#7c3aed", "#db2777", "#0891b2", "#65a30d", "#d97706", "#dc2626", "#4f46e5"];

function colorForLabel(label: string): string {
  let hash = 0;
  for (let i = 0; i < label.length; i++) hash = (hash * 31 + label.charCodeAt(i)) >>> 0;
  return INITIAL_BADGE_COLORS[hash % INITIAL_BADGE_COLORS.length];
}

/** Zero-dependency stand-in for a per-service/per-provider logo — this repo
 * has no icon library or asset pipeline, so a colored initial letter is used
 * instead of a real brand icon. */
export function ServiceBadge({ label }: { label: string }) {
  const initial = label.trim().charAt(0).toUpperCase() || "?";
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        width: "1.5rem",
        height: "1.5rem",
        borderRadius: "50%",
        background: colorForLabel(label),
        color: "#fff",
        fontSize: "0.75rem",
        fontWeight: 700,
        marginRight: "0.5rem",
        flexShrink: 0,
      }}
    >
      {initial}
    </span>
  );
}
