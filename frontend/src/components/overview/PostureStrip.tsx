import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { Posture } from "../../api/overview";
import { freshnessRole, PolicyLadder, ReportFreshnessValue, RiskTile } from "./widgets";

function complianceRole(pct: number | null): "good" | "warning" | "serious" | "critical" | undefined {
  if (pct === null) return undefined;
  if (pct >= 90) return "good";
  if (pct >= 70) return "warning";
  if (pct >= 50) return "serious";
  return "critical";
}

export default function PostureStrip({ domainId, days }: { domainId: string | null; days: number }) {
  const { data, isLoading } = useQuery({
    queryKey: ["dmarc-posture", domainId, days],
    queryFn: () => api.get<Posture>(`/dmarc/posture?days=${days}${domainId ? `&domain_id=${domainId}` : ""}`),
  });

  if (isLoading || !data) {
    return (
      <div className="card">
        <p className="muted" style={{ marginBottom: 0 }}>
          Loading posture…
        </p>
      </div>
    );
  }

  return (
    <div className="card kpi-band">
      <div className="stat-row kpi-band-tiles">
        <RiskTile
          label="Compliance"
          value={data.compliance_pct === null ? "—" : `${data.compliance_pct}%`}
          role={complianceRole(data.compliance_pct)}
        />
        <RiskTile
          label="Failed volume"
          value={data.failed_volume.toLocaleString()}
          role={data.failed_volume > 0 ? "warning" : "good"}
        />
        <RiskTile
          label="New senders"
          value={String(data.new_sender_count)}
          role={data.new_sender_count > 0 ? "warning" : "good"}
        />
        <RiskTile
          label="Last report"
          value={<ReportFreshnessValue hours={data.report_freshness_hours} />}
          role={freshnessRole(data.report_freshness_hours)}
        />
        <RiskTile
          label="Ready to enforce"
          value={String(data.ready_to_enforce_count)}
          role={data.ready_to_enforce_count > 0 ? "good" : undefined}
        />
      </div>
      <div className="kpi-band-policy">
        <div className="stat-tile-label">Current policy</div>
        <PolicyLadder currentPolicy={data.current_policy} distribution={data.policy_distribution} />
      </div>
    </div>
  );
}
