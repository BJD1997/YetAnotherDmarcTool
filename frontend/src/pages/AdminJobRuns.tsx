import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { api } from "../api/client";
import { ReportFreshnessValue, RiskTile } from "../components/overview/widgets";

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

interface JobRunsSummary {
  last_failure: { job_type: string; organization_id: string | null; started_at: string; error_message: string | null } | null;
  success_rate_pct_24h: number | null;
  latest_mailbox_poll_at: string | null;
  reports_processed_today: number;
}

const JOB_TYPES = ["mailbox_poll", "dns_check"];
const STATUSES = ["success", "failure"];
const SINCE_OPTIONS = [
  { label: "Any time", value: "" },
  { label: "Last 24h", value: "1" },
  { label: "Last 7 days", value: "7" },
  { label: "Last 30 days", value: "30" },
];

export default function AdminJobRuns() {
  const [limit, setLimit] = useState(50);
  const [orgFilter, setOrgFilter] = useState("");
  const [jobTypeFilter, setJobTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [sinceFilter, setSinceFilter] = useState("");

  const { data: orgs } = useQuery({
    queryKey: ["admin-organizations"],
    queryFn: () => api.get<AdminOrganization[]>("/admin/organizations"),
  });
  const orgNameById = new Map((orgs ?? []).map((o) => [o.id, o.name]));

  const { data: summary } = useQuery({
    queryKey: ["admin-job-runs-summary"],
    queryFn: () => api.get<JobRunsSummary>("/admin/job-runs/summary"),
  });

  const params = new URLSearchParams({ limit: String(limit) });
  if (orgFilter) params.set("organization_id", orgFilter);
  if (jobTypeFilter) params.set("job_type", jobTypeFilter);
  if (statusFilter) params.set("status", statusFilter);
  if (sinceFilter) params.set("since_days", sinceFilter);

  const { data: runs, isLoading, isFetching, refetch } = useQuery({
    queryKey: ["admin-job-runs", params.toString()],
    queryFn: () => api.get<JobRun[]>(`/admin/job-runs?${params.toString()}`),
  });

  return (
    <section>
      <div className="page-header">
        <h1>Job runs</h1>
      </div>

      {summary && (
        <div className="card">
          <div className="stat-row" style={{ marginBottom: 0 }}>
            <RiskTile
              label="Success rate (24h)"
              value={summary.success_rate_pct_24h === null ? "—" : `${summary.success_rate_pct_24h}%`}
              role={
                summary.success_rate_pct_24h === null
                  ? undefined
                  : summary.success_rate_pct_24h >= 99
                    ? "good"
                    : summary.success_rate_pct_24h >= 90
                      ? "warning"
                      : "critical"
              }
            />
            <RiskTile label="Reports processed today" value={String(summary.reports_processed_today)} />
            <div>
              <div className="stat-tile-value num">
                {summary.latest_mailbox_poll_at ? (
                  <ReportFreshnessValue hours={(Date.now() - new Date(summary.latest_mailbox_poll_at).getTime()) / 3_600_000} />
                ) : (
                  "—"
                )}
              </div>
              <div className="stat-tile-label">Since last mailbox poll</div>
            </div>
            <RiskTile
              label="Last failure"
              value={summary.last_failure ? new Date(summary.last_failure.started_at).toLocaleDateString() : "None recorded"}
              role={summary.last_failure ? "critical" : "good"}
            />
          </div>
          {summary.last_failure && (
            <p className="muted" style={{ marginTop: "0.6rem", marginBottom: 0, fontSize: "0.82rem" }}>
              {summary.last_failure.job_type} for{" "}
              {summary.last_failure.organization_id ? orgNameById.get(summary.last_failure.organization_id) ?? summary.last_failure.organization_id : "—"}
              {summary.last_failure.error_message ? `: ${summary.last_failure.error_message}` : ""}
            </p>
          )}
        </div>
      )}

      <div className="field-row" style={{ marginBottom: "1.25rem" }}>
        <select className="input" value={orgFilter} onChange={(e) => setOrgFilter(e.target.value)}>
          <option value="">All organizations</option>
          {(orgs ?? []).map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
        <select className="input" value={jobTypeFilter} onChange={(e) => setJobTypeFilter(e.target.value)}>
          <option value="">All job types</option>
          {JOB_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <select className="input" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select className="input" value={sinceFilter} onChange={(e) => setSinceFilter(e.target.value)}>
          {SINCE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
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
      {runs && runs.length === 0 && <p className="empty-state">No job runs match these filters.</p>}

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
                {runs.map((run) => {
                  const notable = run.status === "failure" || (typeof run.stats?.errors === "number" && run.stats.errors > 0);
                  return (
                    <tr key={run.id} style={notable ? { background: "var(--critical-wash)" } : undefined}>
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
                        {run.status === "failure" && run.error_message && (
                          <div style={{ color: "var(--critical-text)", marginBottom: run.stats ? "0.25rem" : 0 }}>{run.error_message}</div>
                        )}
                        {run.stats && Object.keys(run.stats).length > 0 && (
                          <div className="chip-row" style={{ fontSize: "0.78rem" }}>
                            {Object.entries(run.stats).map(([k, v]) => (
                              <span
                                key={k}
                                className="muted"
                                style={k === "errors" && typeof v === "number" && v > 0 ? { color: "var(--critical-text)" } : undefined}
                              >
                                {k}: {String(v)}
                              </span>
                            ))}
                          </div>
                        )}
                        {!run.error_message && !run.stats && "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </section>
  );
}
