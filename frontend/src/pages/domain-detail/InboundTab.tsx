import { useOutletContext } from "react-router-dom";
import type { Domain } from "../../api/types";
import InboundTable from "../../components/domain/InboundTable";
import { useInboundHosts } from "../../hooks/useDomainInsights";

export default function InboundTab() {
  const domain = useOutletContext<Domain>();

  const { data: inboundHosts } = useInboundHosts(domain.id, domain.verification_status === "verified");

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
