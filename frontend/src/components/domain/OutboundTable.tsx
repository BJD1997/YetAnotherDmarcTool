import type { DmarcOutboundService } from "../../api/dmarc";
import { ServiceBadge, riskScore, passRateStyle } from "./shared";

function pct(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(1)}%`;
}

export default function OutboundTable({ services }: { services: DmarcOutboundService[] }) {
  if (services.length === 0) {
    return <p className="empty-state">No aggregate reports received yet for this domain.</p>;
  }

  const sorted = [...services].sort((a, b) => riskScore(b) - riskScore(a));

  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Service</th>
            <th>Volume</th>
            <th>SPF aligned</th>
            <th>DKIM aligned</th>
            <th>Accepted</th>
            <th>Quarantined</th>
            <th>Rejected</th>
            <th>DMARC pass</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((s) => (
            <tr key={s.service_label}>
              <td>
                <ServiceBadge label={s.service_label} />
                {s.service_label}
                {s.source_ip_count > 1 && <span className="muted" style={{ fontSize: "0.8rem" }}> ({s.source_ip_count} IPs)</span>}
              </td>
              <td className="num">{s.volume}</td>
              <td className="num">{pct(s.spf_aligned_pct)}</td>
              <td className="num">{pct(s.dkim_aligned_pct)}</td>
              <td className="num">{s.accepted}</td>
              <td className="num" style={{ color: s.quarantined > 0 ? "var(--warning-text)" : undefined }}>
                {s.quarantined}
              </td>
              <td className="num" style={{ color: s.rejected > 0 ? "var(--critical-text)" : undefined }}>
                {s.rejected}
              </td>
              <td className="num" style={{ fontWeight: 600 }}>
                <span style={passRateStyle(s.dmarc_pass_pct)}>{pct(s.dmarc_pass_pct)}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
