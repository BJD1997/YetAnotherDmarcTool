import type { DmarcOutboundService } from "../../api/dmarc";
import { ServiceBadge } from "./shared";

function pct(value: number | null): string {
  return value === null ? "—" : `${value.toFixed(1)}%`;
}

export default function OutboundTable({ services }: { services: DmarcOutboundService[] }) {
  if (services.length === 0) {
    return <p style={{ color: "#6b7280" }}>No aggregate reports received yet for this domain.</p>;
  }

  return (
    <table style={{ borderCollapse: "collapse", width: "100%" }}>
      <thead>
        <tr style={{ textAlign: "left", borderBottom: "2px solid #e5e7eb" }}>
          <th style={{ padding: "0.4rem 1rem 0.4rem 0" }}>Service</th>
          <th style={{ padding: "0.4rem 1rem" }}>Volume</th>
          <th style={{ padding: "0.4rem 1rem" }}>SPF aligned</th>
          <th style={{ padding: "0.4rem 1rem" }}>DKIM aligned</th>
          <th style={{ padding: "0.4rem 1rem" }}>Accepted</th>
          <th style={{ padding: "0.4rem 1rem" }}>Quarantined</th>
          <th style={{ padding: "0.4rem 1rem" }}>Rejected</th>
          <th style={{ padding: "0.4rem 1rem" }}>DMARC pass</th>
        </tr>
      </thead>
      <tbody>
        {services.map((s) => (
          <tr key={s.service_label} style={{ borderBottom: "1px solid #f3f4f6" }}>
            <td style={{ padding: "0.5rem 1rem 0.5rem 0" }}>
              <ServiceBadge label={s.service_label} />
              {s.service_label}
              {s.source_ip_count > 1 && (
                <span style={{ color: "#6b7280", fontSize: "0.8rem" }}> ({s.source_ip_count} IPs)</span>
              )}
            </td>
            <td style={{ padding: "0.5rem 1rem" }}>{s.volume}</td>
            <td style={{ padding: "0.5rem 1rem" }}>{pct(s.spf_aligned_pct)}</td>
            <td style={{ padding: "0.5rem 1rem" }}>{pct(s.dkim_aligned_pct)}</td>
            <td style={{ padding: "0.5rem 1rem" }}>{s.accepted}</td>
            <td style={{ padding: "0.5rem 1rem", color: s.quarantined > 0 ? "#92400e" : undefined }}>
              {s.quarantined}
            </td>
            <td style={{ padding: "0.5rem 1rem", color: s.rejected > 0 ? "#991b1b" : undefined }}>{s.rejected}</td>
            <td style={{ padding: "0.5rem 1rem", fontWeight: 600 }}>{pct(s.dmarc_pass_pct)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
