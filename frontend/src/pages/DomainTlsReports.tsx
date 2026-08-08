import { useEffect, useState } from "react";
import { useOutletContext, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../api/client";
import type { Domain } from "../api/types";
import type { TlsRptFilters, TlsRptReportRow, TlsRptSenderSummary, TlsRptSummary } from "../api/dmarc";
import { TLS_RPT_RESULT_TYPES, tlsRptFilterQuery } from "../api/dmarc";
import { DATE_RANGE_PRESETS } from "../api/overview";
import { Stat } from "../components/domain/shared";

const GROUPINGS = [
  { key: "day", label: "Day" },
  { key: "sender", label: "Reporting org" },
] as const;
type Grouping = (typeof GROUPINGS)[number]["key"];

export default function DomainTlsReports() {
  const domain = useOutletContext<Domain>();
  const domainId = domain.id;
  const [params, setParams] = useSearchParams();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const grouping: Grouping = (params.get("group") as Grouping) || "day";
  const filters: TlsRptFilters = {
    days: params.get("days") ? Number(params.get("days")) : undefined,
    org_name: params.get("org_name") || undefined,
    result_type: params.get("result_type") || undefined,
    failures_only: params.get("failures_only") === "1",
  };
  const filterQS = tlsRptFilterQuery(filters);

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  const summaryQuery = useQuery({
    queryKey: ["tls-rpt-summary", domainId, filterQS],
    queryFn: () => api.get<TlsRptSummary>(`/domains/${domainId}/dmarc/tls-rpt/summary${filterQS ? `?${filterQS}` : ""}`),
  });

  const reportsQuery = useQuery({
    queryKey: ["tls-rpt-reports", domainId, filterQS],
    queryFn: () => api.get<TlsRptReportRow[]>(`/domains/${domainId}/dmarc/tls-rpt/reports${filterQS ? `?${filterQS}` : ""}`),
    enabled: grouping === "day",
  });

  const bySenderQuery = useQuery({
    queryKey: ["tls-rpt-by-sender", domainId, filterQS],
    queryFn: () => api.get<TlsRptSenderSummary[]>(`/domains/${domainId}/dmarc/tls-rpt/by-sender${filterQS ? `?${filterQS}` : ""}`),
    enabled: grouping === "sender",
  });

  const days = groupByDay(reportsQuery.data ?? []);

  return (
    <section>
      <div className="page-header">
        <div>
          <h2>TLS delivery reports</h2>
        </div>
      </div>

      <FilterBar filters={filters} grouping={grouping} onFilterChange={setFilter} onGroupingChange={(g) => setFilter("group", g)} />

      <SummaryBar summary={summaryQuery.data} isLoading={summaryQuery.isLoading} />

      {grouping === "day" ? (
        <>
          {reportsQuery.isLoading && <p className="muted">Loading…</p>}
          {reportsQuery.isSuccess && days.length === 0 && <p className="empty-state">No TLS-RPT reports match these filters.</p>}

          {days.map((day) => (
            <div key={day.date} className="card" style={{ padding: 0, overflow: "hidden" }}>
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "0.75rem 1.1rem",
                  background: "var(--plane)",
                  borderBottom: "1px solid var(--border)",
                  flexWrap: "wrap",
                  gap: "0.4rem",
                }}
              >
                <strong style={{ fontSize: "0.9rem" }}>
                  {new Date(day.date).toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" })}
                </strong>
                <span className="muted num" style={{ fontSize: "0.8rem" }}>
                  {day.rows.length} report{day.rows.length === 1 ? "" : "s"} · {day.successful} successful · {day.failed} failed
                </span>
              </div>
              {day.rows.map((row) => (
                <div key={row.id} style={{ borderBottom: "1px solid var(--border)" }}>
                  <div
                    onClick={() => setExpandedId(expandedId === row.id ? null : row.id)}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      padding: "0.65rem 1.1rem",
                      cursor: "pointer",
                      gap: "0.75rem",
                      flexWrap: "wrap",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
                      {expandedId === row.id ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                      <strong style={{ fontSize: "0.88rem" }}>{row.org_name}</strong>
                      <span className="muted" style={{ fontSize: "0.82rem" }}>
                        policy: {row.policy_type}
                      </span>
                    </div>
                    <div className="num" style={{ display: "flex", gap: "1rem", alignItems: "center", fontSize: "0.82rem" }}>
                      <span>{row.successful_session_count.toLocaleString()} successful</span>
                      {row.failed_session_count > 0 ? (
                        <span className="badge badge--critical">{row.failed_session_count.toLocaleString()} failed</span>
                      ) : (
                        <span className="badge badge--good">0 failed</span>
                      )}
                    </div>
                  </div>
                  {expandedId === row.id && (
                    <div style={{ padding: "0.75rem 1.1rem 1rem 2.3rem", background: "var(--plane)", borderTop: "1px solid var(--border)" }}>
                      <FailureDetails details={row.failure_details} />
                    </div>
                  )}
                </div>
              ))}
            </div>
          ))}
        </>
      ) : (
        <BySenderTable rows={bySenderQuery.data} isLoading={bySenderQuery.isLoading} />
      )}
    </section>
  );
}

function groupByDay(rows: TlsRptReportRow[]): { date: string; rows: TlsRptReportRow[]; successful: number; failed: number }[] {
  const byDate = new Map<string, TlsRptReportRow[]>();
  for (const row of rows) {
    const date = row.date_range_begin.slice(0, 10);
    const existing = byDate.get(date);
    if (existing) existing.push(row);
    else byDate.set(date, [row]);
  }
  return Array.from(byDate.entries())
    .sort((a, b) => b[0].localeCompare(a[0]))
    .map(([date, dayRows]) => ({
      date,
      rows: dayRows,
      successful: dayRows.reduce((sum, r) => sum + r.successful_session_count, 0),
      failed: dayRows.reduce((sum, r) => sum + r.failed_session_count, 0),
    }));
}

function FailureDetails({ details }: { details: TlsRptReportRow["failure_details"] }) {
  if (details.length === 0) return <p className="muted" style={{ margin: 0, fontSize: "0.85rem" }}>No failures reported.</p>;

  return (
    <div className="table-wrap">
      <table className="table">
        <thead>
          <tr>
            <th>Result type</th>
            <th>Failed sessions</th>
            <th>Receiving MX</th>
            <th>Receiving IP</th>
            <th>Sending MTA IP</th>
          </tr>
        </thead>
        <tbody>
          {details.map((d, i) => (
            <tr key={i}>
              <td>{d.result_type}</td>
              <td className="num">{d.failed_session_count.toLocaleString()}</td>
              <td style={{ fontFamily: "monospace", fontSize: "0.8rem" }}>{d.receiving_mx_hostname ?? "—"}</td>
              <td style={{ fontFamily: "monospace", fontSize: "0.8rem" }}>{d.receiving_ip ?? "—"}</td>
              <td style={{ fontFamily: "monospace", fontSize: "0.8rem" }}>{d.sending_mta_ip ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FilterBar({
  filters,
  grouping,
  onFilterChange,
  onGroupingChange,
}: {
  filters: TlsRptFilters;
  grouping: Grouping;
  onFilterChange: (key: string, value: string) => void;
  onGroupingChange: (grouping: Grouping) => void;
}) {
  const [orgName, setOrgName] = useState(filters.org_name ?? "");

  useEffect(() => {
    setOrgName(filters.org_name ?? "");
  }, [filters.org_name]);

  return (
    <div className="card">
      <div className="field-row" style={{ marginBottom: "0.6rem" }}>
        <select className="input" value={filters.days ?? ""} onChange={(e) => onFilterChange("days", e.target.value)}>
          <option value="">All time</option>
          {DATE_RANGE_PRESETS.map((d) => (
            <option key={d} value={d}>
              Last {d} days
            </option>
          ))}
        </select>
        <select className="input" value={filters.result_type ?? ""} onChange={(e) => onFilterChange("result_type", e.target.value)}>
          <option value="">Any result type</option>
          {TLS_RPT_RESULT_TYPES.map((rt) => (
            <option key={rt} value={rt}>
              {rt}
            </option>
          ))}
        </select>
        <label style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontSize: "0.85rem" }}>
          <input
            type="checkbox"
            checked={filters.failures_only ?? false}
            onChange={(e) => onFilterChange("failures_only", e.target.checked ? "1" : "")}
          />
          Failures only
        </label>
      </div>
      <form
        className="field-row"
        style={{ marginBottom: "0.6rem" }}
        onSubmit={(e) => {
          e.preventDefault();
          onFilterChange("org_name", orgName);
        }}
      >
        <input
          className="input"
          placeholder="Filter by reporting org (e.g. google)"
          value={orgName}
          onChange={(e) => setOrgName(e.target.value)}
        />
        <button type="submit" className="btn btn--secondary btn--sm">
          Apply
        </button>
      </form>
      <div className="chip-row">
        <span className="muted" style={{ fontSize: "0.8rem" }}>
          Group by:
        </span>
        {GROUPINGS.map((g) => (
          <button
            key={g.key}
            className="btn btn--ghost btn--sm"
            style={grouping === g.key ? { background: "var(--accent-wash)", color: "var(--accent)" } : undefined}
            onClick={() => onGroupingChange(g.key)}
          >
            {g.label}
          </button>
        ))}
      </div>
    </div>
  );
}

function SummaryBar({ summary, isLoading }: { summary: TlsRptSummary | undefined; isLoading: boolean }) {
  if (isLoading) return <p className="muted">Loading summary…</p>;
  if (!summary || summary.total_reports === 0) return null;

  return (
    <div className="card">
      <div className="stat-row">
        <Stat label="Reports" value={summary.total_reports.toLocaleString()} />
        <Stat label="Successful sessions" value={summary.total_successful_sessions.toLocaleString()} />
        <Stat label="Failed sessions" value={summary.total_failed_sessions.toLocaleString()} />
        <Stat label="Failure rate" value={summary.failure_rate_pct !== null ? `${summary.failure_rate_pct}%` : "—"} />
        <Stat label="Reporting orgs" value={summary.distinct_reporting_orgs.toLocaleString()} />
      </div>
      <div className="chip-row" style={{ marginTop: "0.6rem" }}>
        {summary.policy_type && (
          <span className="muted" style={{ fontSize: "0.82rem" }}>
            Current policy: <strong>{summary.policy_type}</strong>
          </span>
        )}
        {summary.last_report_received_at && (
          <span className="muted" style={{ fontSize: "0.82rem" }}>
            Last report: {new Date(summary.last_report_received_at).toLocaleString()}
          </span>
        )}
      </div>
    </div>
  );
}

function BySenderTable({ rows, isLoading }: { rows: TlsRptSenderSummary[] | undefined; isLoading: boolean }) {
  return (
    <div className="card">
      {isLoading && <p className="muted">Loading…</p>}
      {!isLoading && (rows ?? []).length === 0 && <p className="empty-state">No TLS-RPT reports match these filters.</p>}
      {!isLoading && (rows ?? []).length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Reporting org</th>
                <th>Successful</th>
                <th>Failed</th>
                <th>Failure reasons</th>
              </tr>
            </thead>
            <tbody>
              {rows!.map((s) => (
                <tr key={s.org_name}>
                  <td>{s.org_name}</td>
                  <td className="num">{s.successful_session_count.toLocaleString()}</td>
                  <td className="num">
                    {s.failed_session_count > 0 ? (
                      <span className="badge badge--critical">{s.failed_session_count.toLocaleString()}</span>
                    ) : (
                      <span className="badge badge--good">0</span>
                    )}
                  </td>
                  <td>
                    {s.failure_reasons.length === 0 ? (
                      <span className="muted">—</span>
                    ) : (
                      <div className="chip-row">
                        {s.failure_reasons.map((r) => (
                          <span key={r.result_type} className="badge badge--neutral">
                            {r.result_type} ×{r.count}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
