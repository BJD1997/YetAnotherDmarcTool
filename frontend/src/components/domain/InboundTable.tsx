import type { InboundHostRow } from "../../api/dmarc";
import type { CheckStatus } from "../../api/dnsChecks";
import { ServiceBadge, StatusBadge } from "./shared";

function CheckCell({ status }: { status: CheckStatus | null }) {
  if (status === null) return <span className="muted">—</span>;
  return <StatusBadge status={status} />;
}

function MtaStsCell({ status }: { status: InboundHostRow["mta_sts_status"] }) {
  if (status === "pass") return <StatusBadge status="pass" />;
  if (status === "not_covered") return <StatusBadge status="fail" />;
  return <span className="muted">Not configured</span>;
}

export default function InboundTable({ hosts }: { hosts: InboundHostRow[] }) {
  if (hosts.length === 0) {
    return <p className="empty-state">No MX hosts checked yet — run "Recheck now" below.</p>;
  }

  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Priority</th>
            <th>Host</th>
            <th>Provider</th>
            <th>MX</th>
            <th>STARTTLS</th>
            <th>MTA-STS</th>
            <th>DANE</th>
          </tr>
        </thead>
        <tbody>
          {hosts.map((h) => (
            <tr key={h.host}>
              <td className="num">{h.priority ?? "—"}</td>
              <td style={{ fontFamily: "monospace", fontSize: "0.82rem" }}>{h.host}</td>
              <td>
                <ServiceBadge label={h.provider_label} />
                {h.provider_label}
              </td>
              <td>
                <CheckCell status={h.mx_status} />
              </td>
              <td>
                <CheckCell status={h.starttls_status} />
              </td>
              <td>
                <MtaStsCell status={h.mta_sts_status} />
              </td>
              <td>
                <CheckCell status={h.dane_status} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
