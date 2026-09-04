import type { DmarcOutboundService } from "../../api/dmarc";
import { ServiceBadge, riskScore, passRateStyle } from "./shared";

function pct(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(1)}%`;
}

function FcrdnsBadge({ status }: { status: "pass" | "partial" | "fail" }) {
  if (status === "pass") {
    return (
      <span className="muted" title="Every sending IP has forward-confirmed reverse DNS (PTR resolves back to the IP).">
        confirmed
      </span>
    );
  }
  return (
    <span
      className={`badge badge--${status === "fail" ? "serious" : "warning"}`}
      title={
        status === "fail"
          ? "None of this sender's IPs have forward-confirmed reverse DNS (PTR pointing back to the IP)."
          : "Some of this sender's IPs lack forward-confirmed reverse DNS (PTR pointing back to the IP)."
      }
    >
      {status === "fail" ? "rDNS fail" : "rDNS partial"}
    </span>
  );
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
            <th>Reverse DNS</th>
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
                {s.sends_ipv6 && (
                  <span className="badge badge--neutral" style={{ marginLeft: "0.4rem" }} title="Sends over IPv6 — stricter deliverability rules (valid PTR, usually DKIM, required).">
                    IPv6
                  </span>
                )}
              </td>
              <td><FcrdnsBadge status={s.fcrdns_status} /></td>
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
