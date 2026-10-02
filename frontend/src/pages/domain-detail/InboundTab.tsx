import { Link, useOutletContext } from "react-router-dom";
import type { Domain } from "../../api/types";
import InboundTable from "../../components/domain/InboundTable";
import { MAIL_PROFILE_LABELS } from "../../components/domain/shared";
import { useInboundHosts } from "../../hooks/useDomainInsights";

export default function InboundTab() {
  const domain = useOutletContext<Domain>();
  // A domain marked "not used for mail" has no legitimate inbound mail
  // infrastructure to report on at all — same "parked" concept the policy
  // builder and action-queue rules already gate on.
  const isParked = domain.mail_profile === "parked";

  const { data: inboundHosts, isPending } = useInboundHosts(domain.id, domain.verification_status === "verified" && !isParked);

  if (domain.verification_status !== "verified") {
    return <p className="empty-state">Verify this domain to see inbound mail hosts.</p>;
  }

  if (isParked) {
    return (
      <p className="empty-state">
        This domain is marked "{MAIL_PROFILE_LABELS.parked}" — no inbound hosts are expected here.
      </p>
    );
  }

  const hosts = inboundHosts ?? [];

  return (
    <div className="card">
      <h3 className="section-title">Inbound email</h3>
      <p className="section-hint">Hosts that process email for this domain, and whether they enforce TLS.</p>
      {isPending ? (
        <p className="muted">Loading…</p>
      ) : hosts.length === 0 ? (
        <p className="empty-state">
          No MX hosts checked yet — run a check from <Link to={`/domains/${domain.id}/dns`}>DNS checks</Link>.
        </p>
      ) : (
        <InboundTable hosts={hosts} />
      )}
    </div>
  );
}
