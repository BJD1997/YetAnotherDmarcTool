import { Link } from "react-router-dom";
import type { Domain } from "../../api/types";
import { useDomains, useVerifyDomain } from "../../hooks/useDomains";
import CopyValue from "../CopyValue";

// Domains still waiting for their verification TXT record, with what to
// publish. Polls while any is pending so a background verification shows up.
export default function PendingDomains() {
  const { data: domains } = useDomains({
    refetchInterval: (query) =>
      (query.state.data ?? []).some((d) => !d.parent_domain_id && d.verification_status === "pending") ? 10000 : false,
  });
  const pending = (domains ?? []).filter((d) => !d.parent_domain_id && d.verification_status !== "verified");
  if (pending.length === 0) return null;
  return (
    <div style={{ display: "grid", gap: "0.75rem", marginTop: "1rem" }}>
      {pending.map((d) => <PendingDomain key={d.id} domain={d} />)}
      <p className="section-hint" style={{ margin: 0 }}>
        All domains: <Link to="/domains">Domains page</Link>.
      </p>
    </div>
  );
}

function PendingDomain({ domain }: { domain: Domain }) {
  const verify = useVerifyDomain(domain.id);
  return (
    <div className="card" style={{ padding: "0.9rem" }}>
      <strong>{domain.name}</strong> — add this TXT record, then verify:
      <div style={{ display: "grid", gap: "0.3rem", margin: "0.5rem 0" }}>
        <CopyValue label="Host/name" value={domain.verification_record_name} />
        <CopyValue label="Value" value={domain.verification_token} />
      </div>
      <button className="btn btn--secondary btn--sm" onClick={() => verify.mutate()} disabled={verify.isPending}>
        {verify.isPending ? "Checking…" : "Verify now"}
      </button>
      {verify.isSuccess && !verify.data.verified && (
        <p className="section-hint" style={{ color: "var(--critical-text)", marginBottom: 0 }}>
          Not found yet — DNS changes can take a few minutes to propagate.
        </p>
      )}
    </div>
  );
}
