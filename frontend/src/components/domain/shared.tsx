import type { CheckStatus } from "../../api/dnsChecks";
import type { Domain, DomainMailProfile } from "../../api/types";

export function VerificationBadge({ status }: { status: Domain["verification_status"] }) {
  const verified = status === "verified";
  return <span className={`badge ${verified ? "badge--good" : "badge--warning"}`}>{verified ? "verified" : "unverified"}</span>;
}

export const MAIL_PROFILE_LABELS: Record<DomainMailProfile, string> = {
  sends_mail: "Sends mail",
  receive_only: "Receive-only",
  parked: "Not used for mail",
};

export function MailProfileSelect({
  value,
  onChange,
  className,
}: {
  value: DomainMailProfile;
  onChange: (value: DomainMailProfile) => void;
  className?: string;
}) {
  return (
    <select className={className ?? "input"} value={value} onChange={(e) => onChange(e.target.value as DomainMailProfile)}>
      {(Object.entries(MAIL_PROFILE_LABELS) as [DomainMailProfile, string][]).map(([key, label]) => (
        <option key={key} value={key}>
          {label}
        </option>
      ))}
    </select>
  );
}

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

/** Estimated failed-message count (volume x fail rate) — used as the
 * default sort/"worst first" ordering anywhere a list of senders is shown
 * (OutboundTable, SenderInventory), so the worst combination of "sends a
 * lot" and "mostly fails" surfaces first instead of just the biggest
 * sender regardless of health. */
export function riskScore(row: { volume: number; dmarc_pass_pct: number | null }): number {
  const failRate = row.dmarc_pass_pct === null ? 0 : 1 - row.dmarc_pass_pct / 100;
  return row.volume * failRate;
}

/** Bold + tinted-background treatment for a low DMARC pass-rate value, so
 * it stands out in a scan rather than relying on text color alone. */
export function passRateStyle(pct: number | null): { color: string; background: string; fontWeight: number; borderRadius: string; padding: string } | undefined {
  if (pct === null) return undefined;
  if (pct < 50) {
    return { color: "var(--critical-text)", background: "var(--critical-wash)", fontWeight: 700, borderRadius: "4px", padding: "0.1rem 0.4rem" };
  }
  if (pct < 80) {
    return { color: "var(--warning-text)", background: "var(--warning-wash)", fontWeight: 700, borderRadius: "4px", padding: "0.1rem 0.4rem" };
  }
  return undefined;
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
