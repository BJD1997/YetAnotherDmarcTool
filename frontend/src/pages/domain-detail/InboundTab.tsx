import { useOutletContext } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { Domain } from "../../api/types";
import type { InboundHostRow } from "../../api/dmarc";
import InboundTable from "../../components/domain/InboundTable";

export default function InboundTab() {
  const domain = useOutletContext<Domain>();

  const { data: inboundHosts } = useQuery({
    queryKey: ["dmarc-inbound", domain.id],
    queryFn: () => api.get<InboundHostRow[]>(`/domains/${domain.id}/dmarc/inbound`),
    enabled: domain.verification_status === "verified",
  });

  if (domain.verification_status !== "verified") {
    return <p className="empty-state">Verify this domain to see inbound mail hosts.</p>;
  }

  return (
    <div className="card">
      <h3 className="section-title">Inbound email</h3>
      <p className="section-hint">Hosts that process email for this domain, and whether they enforce TLS.</p>
      <InboundTable hosts={inboundHosts ?? []} />
    </div>
  );
}
