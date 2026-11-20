import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { LoadMoreButton } from "../../components/shared/LoadMoreButton";
import { DateRangeFilter, EMPTY_DATE_RANGE, setDateRangeParams } from "../../components/shared/DateRangeFilter";
import { ReportFreshnessValue, RiskTile } from "../../components/overview/widgets";
import { groupFailureStreaks } from "../../components/shared/failureStreaks";
import { type AdminJobRun, useAdminJobRuns, useAdminJobRunsSummary, useAdminOrganizationNames } from "../../hooks/useAdmin";

const JOB_TYPES = ["mailbox_poll", "dns_check"];
const STATUSES = ["success", "failure"];

export default function AdminJobRuns() {
  const [orgFilter, setOrgFilter] = useState("");
  const [jobTypeFilter, setJobTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [dateRange, setDateRange] = useState(EMPTY_DATE_RANGE);
  const [openStreaks, setOpenStreaks] = useState<Set<string>>(new Set());

  const { data: orgs = [] } = useAdminOrganizationNames();
  const orgNameById = new Map(orgs.map((o) => [o.id, o.name]));

  const { data: summary } = useAdminJobRunsSummary();

  const params = new URLSearchParams();
  if (orgFilter) params.set("organization_id", orgFilter);
  if (jobTypeFilter) params.set("job_type", jobTypeFilter);
  if (statusFilter) params.set("status", statusFilter);
  setDateRangeParams(params, dateRange);

  const query = useAdminJobRuns(params.toString());
  const runs = query.data?.pages.flatMap((p) => p.job_runs) ?? [];
  const rows = groupFailureStreaks(runs, (r) => `${r.job_type}|${r.organization_id ?? ""}|${r.domain_id ?? ""}`);
  const toggleStreak = (id: string) =>
    setOpenStreaks((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
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
              label={summary.last_failure?.recovered ? "Last failure (recovered)" : "Last failure"}
              value={summary.last_failure ? new Date(summary.last_failure.started_at).toLocaleDateString() : "None recorded"}
              role={!summary.last_failure ? "good" : summary.last_failure.recovered ? undefined : "critical"}
            />
          </div>
          {summary.last_failure && (
            <p className="muted" style={{ marginTop: "0.6rem", marginBottom: 0, fontSize: "0.82rem" }}>
              {summary.last_failure.recovered ? "Recovered: a later run succeeded. " : ""}
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
          {orgs.map((o) => (
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
        <DateRangeFilter value={dateRange} onChange={setDateRange} />
        <button className="btn btn--secondary btn--sm" onClick={() => query.refetch()} disabled={query.isFetching}>
          <RefreshCw />
          {query.isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {query.isLoading && <p className="muted">Loading…</p>}
      {query.isSuccess && runs.length === 0 && <p className="empty-state">No job runs match these filters.</p>}

      {runs.length > 0 && (
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
                {rows.map(({ run, streak }) => {
                  const open = streak !== undefined && openStreaks.has(run.id);
                  return [
                    <JobRunRow
                      key={run.id}
                      run={run}
                      orgName={orgNameById}
                      streak={streak}
                      open={open}
                      onToggle={() => toggleStreak(run.id)}
                    />,
                    ...(open && streak ? streak.map((r) => <JobRunRow key={`${run.id}:${r.id}`} run={r} orgName={orgNameById} nested />) : []),
                  ];
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <LoadMoreButton hasNextPage={query.hasNextPage} isFetchingNextPage={query.isFetchingNextPage} onClick={() => query.fetchNextPage()} />
    </section>
  );
}

function JobRunRow({
  run,
  orgName,
  streak,
  open = false,
  onToggle,
  nested = false,
}: {
  run: AdminJobRun;
  orgName: Map<string, string>;
  /** Every failure folded into this row, newest first (2 or more). */
  streak?: AdminJobRun[];
  open?: boolean;
  onToggle?: () => void;
  /** One run of an expanded streak, shown under its summary row. */
  nested?: boolean;
}) {
  const notable = run.status === "failure" || (typeof run.stats?.errors === "number" && run.stats.errors > 0);
  const oldest = streak?.[streak.length - 1];
  return (
    <tr style={notable ? { background: "var(--critical-wash)", opacity: nested ? 0.85 : undefined } : undefined}>
      <td style={nested ? { paddingLeft: "1.5rem" } : undefined}>{run.job_type}</td>
      <td>{run.organization_id ? (orgName.get(run.organization_id) ?? run.organization_id) : "—"}</td>
      <td style={{ whiteSpace: "nowrap" }}>
        <span className={`badge ${run.status === "success" ? "badge--good" : "badge--critical"}`}>
          {streak ? `${run.status} ×${streak.length}` : run.status}
        </span>
      </td>
      <td style={{ whiteSpace: "nowrap" }} className="num">
        {new Date(run.started_at).toLocaleString()}
        {oldest && <div className="muted" style={{ fontSize: "0.78rem" }}>since {new Date(oldest.started_at).toLocaleString()}</div>}
      </td>
      <td style={{ whiteSpace: "nowrap" }} className="num">
        {run.finished_at ? `${((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000).toFixed(1)}s` : "—"}
      </td>
      <td>
        {run.status === "failure" && run.error_message && (
          <div style={{ color: "var(--critical-text)", marginBottom: run.stats ? "0.25rem" : 0 }}>
            {streak ? "Latest: " : ""}
            {run.error_message}
          </div>
        )}
        {!streak && run.stats && Object.keys(run.stats).length > 0 && (
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
        {streak && (
          <button type="button" className="btn btn--secondary btn--sm" style={{ marginTop: "0.35rem" }} onClick={onToggle} aria-expanded={open}>
            {open ? "Hide the runs" : `Show all ${streak.length} runs`}
          </button>
        )}
        {!run.error_message && !run.stats && !streak && "—"}
      </td>
    </tr>
  );
}
