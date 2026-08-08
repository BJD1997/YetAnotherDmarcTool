import { useParams, Link, NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { api } from "../../api/client";
import type { Domain } from "../../api/types";
import { VerificationBadge } from "../../components/domain/shared";

const TABS: { to: string; label: string; end?: boolean }[] = [
  { to: "", label: "Overview", end: true },
  { to: "fixes", label: "Fixes" },
  { to: "dns", label: "DNS checks" },
  { to: "senders", label: "Senders" },
  { to: "inbound", label: "Inbound" },
  { to: "reports", label: "Reports" },
  { to: "tls-reports", label: "TLS reports" },
];

export default function DomainDetailLayout() {
  const { domainId } = useParams<{ domainId: string }>();

  const { data: domain } = useQuery({
    queryKey: ["domains", domainId],
    queryFn: () => api.get<Domain>(`/domains/${domainId}`),
    enabled: !!domainId,
  });

  if (!domainId) return null;

  return (
    <section>
      <Link to="/domains" className="back-link">
        <ArrowLeft size={14} />
        Domains
      </Link>
      <div className="page-header">
        <h1 style={{ display: "flex", alignItems: "center", gap: "0.6rem" }}>
          {domain?.name ?? "…"}
          {domain && <VerificationBadge status={domain.verification_status} />}
        </h1>
      </div>

      <nav className="detail-tabs">
        {TABS.map((tab) => (
          <NavLink
            key={tab.to}
            to={tab.to || "."}
            end={tab.end}
            className={({ isActive }) => `detail-tab ${isActive ? "active" : ""}`}
          >
            {tab.label}
          </NavLink>
        ))}
      </nav>

      {domain ? <Outlet context={domain} /> : <p className="muted">Loading…</p>}
    </section>
  );
}
