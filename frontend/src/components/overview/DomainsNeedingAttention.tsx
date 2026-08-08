import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../../api/client";
import type { RankedDomain } from "../../api/overview";

function gradeRole(item: RankedDomain): "good" | "warning" | "serious" | "critical" {
  if (item.score === null) return "critical";
  if (item.score >= 90) return "good";
  if (item.score >= 70) return "warning";
  if (item.score >= 50) return "serious";
  return "critical";
}

export default function DomainsNeedingAttention() {
  const { data, isLoading } = useQuery({
    queryKey: ["domains-ranked"],
    queryFn: () => api.get<RankedDomain[]>("/domains/ranked"),
  });

  const items = (data ?? []).filter((d) => d.not_verified || (d.score !== null && d.score < 90)).slice(0, 8);

  return (
    <div style={{ marginTop: "1rem", paddingTop: "1rem", borderTop: "1px solid var(--border)" }}>
      <div className="card-header">
        <h3>Domains needing attention</h3>
      </div>
      {isLoading && <p className="muted">Loading…</p>}
      {!isLoading && items.length === 0 && <p className="empty-state">Every domain is in good shape.</p>}
      <div style={{ display: "flex", flexDirection: "column", gap: "0.15rem" }}>
        {items.map((d) => (
          <Link
            key={d.domain_id}
            to={`/domains/${d.domain_id}`}
            className="chip-row"
            style={{ justifyContent: "space-between", color: "inherit", textDecoration: "none", padding: "0.4rem 0" }}
          >
            <span>{d.name}</span>
            {d.not_verified ? (
              <span className="badge badge--critical">unverified</span>
            ) : d.insufficient_data ? (
              <span className="badge badge--neutral">no data</span>
            ) : (
              <span className={`badge badge--${gradeRole(d)}`}>
                {d.grade} · {d.score}%
              </span>
            )}
          </Link>
        ))}
      </div>
    </div>
  );
}
