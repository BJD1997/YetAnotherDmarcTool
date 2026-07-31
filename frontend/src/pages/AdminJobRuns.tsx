import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
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
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 1100, margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>Job Runs</h1>
        <Link to="/admin">&larr; Back to organizations</Link>
      </div>

      <div style={{ display: "flex", gap: "0.75rem", alignItems: "center", margin: "1rem 0" }}>
        <label style={{ fontSize: "0.85rem", color: "#6b7280" }}>
          Show last{" "}
          <select value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
            <option value={200}>200</option>
          </select>
        </label>
        <button onClick={() => refetch()} disabled={isFetching}>
          {isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {isLoading && <p>Loading…</p>}

      {runs && runs.length === 0 && <p style={{ color: "#6b7280" }}>No job runs recorded yet.</p>}

      {runs && runs.length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.9rem" }}>
          <thead>
            <tr style={{ textAlign: "left", borderBottom: "2px solid #e5e7eb" }}>
              <th style={{ padding: "0.5rem" }}>Job</th>
              <th style={{ padding: "0.5rem" }}>Organization</th>
              <th style={{ padding: "0.5rem" }}>Status</th>
              <th style={{ padding: "0.5rem" }}>Started</th>
              <th style={{ padding: "0.5rem" }}>Duration</th>
              <th style={{ padding: "0.5rem" }}>Details</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={run.id} style={{ borderBottom: "1px solid #f3f4f6" }}>
                <td style={{ padding: "0.5rem" }}>{run.job_type}</td>
                <td style={{ padding: "0.5rem" }}>
                  {run.organization_id ? (orgNameById.get(run.organization_id) ?? run.organization_id) : "—"}
                </td>
                <td style={{ padding: "0.5rem" }}>
                  <span style={{ color: run.status === "success" ? "#166534" : "#991b1b", fontWeight: 600 }}>
                    {run.status}
                  </span>
                </td>
                <td style={{ padding: "0.5rem", whiteSpace: "nowrap" }}>{new Date(run.started_at).toLocaleString()}</td>
                <td style={{ padding: "0.5rem", whiteSpace: "nowrap" }}>
                  {run.finished_at
                    ? `${((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000).toFixed(1)}s`
                    : "—"}
                </td>
                <td style={{ padding: "0.5rem" }}>
                  {run.status === "failure" && run.error_message ? (
                    <span style={{ color: "#b91c1c" }}>{run.error_message}</span>
                  ) : run.stats && Object.keys(run.stats).length > 0 ? (
                    <span style={{ color: "#6b7280" }}>
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
      )}
    </main>
  );
}
