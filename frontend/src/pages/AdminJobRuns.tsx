import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { api } from "../api/client";

interface JobRun {
  id: string;
  job_type: string;
  organization_id: string | null;
  domain_id: string | null;
  status: "success" | "failure";
  started_at: string;
  finished_at: string | null;
  error_message: string | null;
  stats: Record<string, unknown> | null;
}

interface AdminOrganization {
  id: string;
  name: string;
}

export default function AdminJobRuns() {
  const [limit, setLimit] = useState(50);

  const { data: orgs } = useQuery({
    queryKey: ["admin-organizations"],
    queryFn: () => api.get<AdminOrganization[]>("/admin/organizations"),
  });
  const orgNameById = new Map((orgs ?? []).map((o) => [o.id, o.name]));

  const { data: runs, isLoading, isFetching, refetch } = useQuery({
    queryKey: ["admin-job-runs", limit],
    queryFn: () => api.get<JobRun[]>(`/admin/job-runs?limit=${limit}`),
  });

  return (
    <section>
      <div className="page-header">
        <h1>Job runs</h1>
      </div>

      <div className="field-row" style={{ marginBottom: "1.25rem" }}>
        <label className="muted" style={{ fontSize: "0.85rem", display: "flex", alignItems: "center", gap: "0.4rem" }}>
          Show last
          <select className="input" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
            <option value={200}>200</option>
          </select>
        </label>
        <button className="btn btn--secondary btn--sm" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw />
          {isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {isLoading && <p className="muted">Loading…</p>}
      {runs && runs.length === 0 && <p className="empty-state">No job runs recorded yet.</p>}

      {runs && runs.length > 0 && (
        <div className="card" style={{ padding: 0 }}>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Organization</th>
                  <th>Status</th>
                  <th>Started</th>
                  <th>Duration</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td>{run.job_type}</td>
                    <td>{run.organization_id ? (orgNameById.get(run.organization_id) ?? run.organization_id) : "—"}</td>
                    <td>
                      <span className={`badge ${run.status === "success" ? "badge--good" : "badge--critical"}`}>{run.status}</span>
                    </td>
                    <td style={{ whiteSpace: "nowrap" }} className="num">
                      {new Date(run.started_at).toLocaleString()}
                    </td>
                    <td style={{ whiteSpace: "nowrap" }} className="num">
                      {run.finished_at
                        ? `${((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000).toFixed(1)}s`
                        : "—"}
                    </td>
                    <td>
                      {run.status === "failure" && run.error_message ? (
                        <span style={{ color: "var(--critical-text)" }}>{run.error_message}</span>
                      ) : run.stats && Object.keys(run.stats).length > 0 ? (
                        <span className="muted">
                          {Object.entries(run.stats)
                            .map(([k, v]) => `${k}=${v}`)
                            .join(", ")}
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
