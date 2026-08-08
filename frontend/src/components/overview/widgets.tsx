import type { ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { MailboxConnectionStatus } from "../../api/dmarc";
import type { Organization } from "../../api/types";

const POLICY_RUNGS = ["none", "quarantine", "reject"];
// Strictest first for the distribution pills — matches how you'd scan "how
// enforced are we" at a glance, distinct from the ladder below which reads
// least-to-most-strict to visualize progress toward enforcement.
const POLICY_RUNGS_DESC = ["reject", "quarantine", "none"];

export function PolicyLadder({
  currentPolicy,
  distribution,
}: {
  currentPolicy: string | null;
  distribution: Record<string, number> | null;
}) {
  if (distribution) {
    const total = Object.values(distribution).reduce((a, b) => a + b, 0);
    if (total === 0) return <span className="muted">No policy data yet</span>;
    return (
      <div className="chip-row">
        {POLICY_RUNGS_DESC.map((rung) => (
          <span key={rung} className="badge badge--neutral" style={!distribution[rung] ? { opacity: 0.5 } : undefined}>
            {rung}: {distribution[rung] ?? 0}
          </span>
        ))}
      </div>
    );
  }

  if (!currentPolicy) {
    return <span className="muted">No policy published</span>;
  }

  return (
    <div className="chip-row">
      {POLICY_RUNGS.map((rung) => (
        <span key={rung} className={`badge ${rung === currentPolicy ? "badge--good" : "badge--neutral"}`}>
          p={rung}
        </span>
      ))}
    </div>
  );
}

export function MailboxHealthWidget({
  connection,
  isLoading,
}: {
  connection: MailboxConnectionStatus | undefined;
  isLoading?: boolean;
}) {
  // Same cached query key used across the app (Settings/PolicyBuilder/
  // Onboarding) — react-query dedupes this into the existing fetch rather
  // than adding a new request, so every call site of this widget gets
  // hosted-mailbox awareness for free without threading org through props.
  const { data: org } = useQuery({
    queryKey: ["organization", "current"],
    queryFn: () => api.get<Organization>("/organizations/current"),
  });

  // Distinct from "not configured": the query hasn't resolved yet, so
  // `connection` being undefined doesn't yet mean anything — showing "not
  // configured" here reads as a real setup problem and can needlessly
  // worry a user who briefly sees it on every page load.
  if (isLoading) {
    return <span className="badge badge--neutral">Checking mailbox…</span>;
  }
  if (!connection) {
    // A local-auth org (or an Entra org that's opted in) has no
    // MailboxConnection at all by design — reports arrive via a hosted
    // address per domain instead (see Settings), so this isn't a real
    // problem worth a warning badge.
    if (org && (!org.entra_tenant_id || org.hosted_mailbox_opt_in)) {
      return <span className="badge badge--good">Using hosted mailbox</span>;
    }
    return <span className="badge badge--warning">Mailbox not configured</span>;
  }
  if (connection.last_sync_status === "error") {
    return <span className="badge badge--critical">Mailbox sync failing</span>;
  }
  if (!connection.last_sync_at) {
    return <span className="badge badge--warning">Mailbox never synced</span>;
  }
  return <span className="badge badge--good">Mailbox healthy</span>;
}

export function ReportFreshnessValue({ hours }: { hours: number | null }) {
  if (hours === null) return <>—</>;
  if (hours < 1) return <>{Math.max(1, Math.round(hours * 60))}m</>;
  if (hours < 48) return <>{Math.round(hours)}h</>;
  return <>{Math.round(hours / 24)}d</>;
}

type RiskRole = "good" | "warning" | "serious" | "critical";

// Aggregate reports typically arrive daily, so recent is healthy and only
// a real multi-day gap should read as risk — matches
// mailbox_stopped_receiving_reports' own STALE_MAILBOX_DAYS=10 window on
// the backend, just with an earlier "starting to look stale" warning step.
export function freshnessRole(hours: number | null): RiskRole | undefined {
  if (hours === null) return undefined;
  if (hours < 48) return "good";
  if (hours < 120) return "warning";
  return "critical";
}

export function RiskTile({ label, value, role }: { label: string; value: ReactNode; role?: RiskRole }) {
  return (
    <div className={role ? `risk-tile risk-tile--${role}` : undefined}>
      <div className="stat-tile-value num">
        {role && <span className={`dot dot--${role}`} />}
        {value}
      </div>
      <div className="stat-tile-label">{label}</div>
    </div>
  );
}
