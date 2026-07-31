import type { InboundHostRow } from "../../api/dmarc";
import type { CheckStatus } from "../../api/dnsChecks";
import { ServiceBadge, StatusBadge } from "./shared";

function CheckCell({ status }: { status: CheckStatus | null }) {
  if (status === null) return <span style={{ color: "#9ca3af" }}>—</span>;
  return <StatusBadge status={status} />;
}

function MtaStsCell({ status }: { status: InboundHostRow["mta_sts_status"] }) {
  if (status === "pass") return <StatusBadge status="pass" />;
  if (status === "not_covered") return <StatusBadge status="fail" />;
  return <span style={{ color: "#9ca3af" }}>Not configured</span>;
}

export default function InboundTable({ hosts }: { hosts: InboundHostRow[] }) {
  if (hosts.length === 0) {
    return <p style={{ color: "#6b7280" }}>No MX hosts checked yet — run "Recheck now" below.</p>;
  }

  return (
    <table style={{ borderCollapse: "collapse", width: "100%" }}>
      <thead>
        <tr style={{ textAlign: "left", borderBottom: "2px solid #e5e7eb" }}>
          <th style={{ padding: "0.4rem 1rem 0.4rem 0" }}>Priority</th>
          <th style={{ padding: "0.4rem 1rem" }}>Host</th>
          <th style={{ padding: "0.4rem 1rem" }}>Provider</th>
          <th style={{ padding: "0.4rem 1rem" }}>MX</th>
          <th style={{ padding: "0.4rem 1rem" }}>STARTTLS</th>
          <th style={{ padding: "0.4rem 1rem" }}>MTA-STS</th>
          <th style={{ padding: "0.4rem 1rem" }}>DANE</th>
        </tr>
      </thead>
      <tbody>
        {hosts.map((h) => (
          <tr key={h.host} style={{ borderBottom: "1px solid #f3f4f6" }}>
            <td style={{ padding: "0.5rem 1rem 0.5rem 0" }}>{h.priority ?? "—"}</td>
            <td style={{ padding: "0.5rem 1rem", fontFamily: "monospace", fontSize: "0.85rem" }}>{h.host}</td>
            <td style={{ padding: "0.5rem 1rem" }}>
              <ServiceBadge label={h.provider_label} />
              {h.provider_label}
            </td>
            <td style={{ padding: "0.5rem 1rem" }}>
              <CheckCell status={h.mx_status} />
            </td>
            <td style={{ padding: "0.5rem 1rem" }}>
              <CheckCell status={h.starttls_status} />
            </td>
            <td style={{ padding: "0.5rem 1rem" }}>
              <MtaStsCell status={h.mta_sts_status} />
            </td>
            <td style={{ padding: "0.5rem 1rem" }}>
              <CheckCell status={h.dane_status} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
