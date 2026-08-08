import { useEffect, useMemo, useState } from "react";
import { useOutletContext, useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../api/client";
import type { Domain } from "../api/types";
import type {
  DmarcDayGroup,
  DmarcDayRow,
  DmarcRecordDetail,
  DmarcReportsByDay,
  DmarcReportsGroupedRow,
  DmarcReportsSummary,
  ReportsFilters,
} from "../api/dmarc";
import { reportsFilterQuery } from "../api/dmarc";
import { DATE_RANGE_PRESETS } from "../api/overview";
import { Stat } from "../components/domain/shared";

function dispositionRole(disposition: string): "good" | "warning" | "critical" {
  if (disposition === "reject") return "critical";
  if (disposition === "quarantine") return "warning";
  return "good";
}

function resultColor(result: string): string {
  return result === "pass" ? "var(--good-text)" : "var(--critical-text)";
}

function IpLink({ ip }: { ip: string }) {
  return (
    <a
      href={`https://ipinfo.io/${ip}`}
      target="_blank"
      rel="noreferrer"
      onClick={(e) => e.stopPropagation()}
      style={{ color: "inherit" }}
    >
      {ip}
    </a>
  );
}

// A row is "bad" if it was ever quarantined/rejected, or if it only passed
// DMARC through one of the two mechanisms rather than both — surfaced as a
// left-border/background tint so it visually separates from the sea of
// clean-pass rows, same treatment as Sender Inventory's low-pass-rate cells.
function rowRole(row: DmarcDayRow): "good" | "warning" | "critical" | null {
  if (row.disposition !== "none") return dispositionRole(row.disposition);
  if (row.spf_result === "fail" || row.dkim_result === "fail") return "warning";
  return null;
}

const GROUPINGS = [
  { key: "day", label: "Day" },
  { key: "source", label: "Source" },
  { key: "reporter", label: "Reporter" },
  { key: "disposition", label: "Disposition" },
] as const;
type Grouping = (typeof GROUPINGS)[number]["key"];

export default function DomainReports() {
  const domain = useOutletContext<Domain>();
  const domainId = domain.id;
  const [params, setParams] = useSearchParams();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const grouping: Grouping = (params.get("group") as Grouping) || "day";
  const filters: ReportsFilters = {
    days: params.get("days") ? Number(params.get("days")) : undefined,
    disposition: (params.get("disposition") as ReportsFilters["disposition"]) || undefined,
    spf_result: (params.get("spf_result") as ReportsFilters["spf_result"]) || undefined,
    dkim_result: (params.get("dkim_result") as ReportsFilters["dkim_result"]) || undefined,
    reporter: params.get("reporter") || undefined,
    source_ip: params.get("source_ip") || undefined,
  };
  const filterQS = reportsFilterQuery(filters);

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  const summaryQuery = useQuery({
    queryKey: ["dmarc-reports-summary", domainId, filterQS],
    queryFn: () => api.get<DmarcReportsSummary>(`/domains/${domainId}/dmarc/reports/summary${filterQS ? `?${filterQS}` : ""}`),
  });

  const daysQuery = useInfiniteQuery({
    queryKey: ["dmarc-reports-by-day", domainId, filterQS],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) => {
      const qs = new URLSearchParams(filterQS);
      if (pageParam) qs.set("before_id", pageParam);
      return api.get<DmarcReportsByDay>(`/domains/${domainId}/dmarc/reports/by-day?${qs.toString()}`);
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => {
      if (!lastPage.has_more) return undefined;
      const lastDay = lastPage.days[lastPage.days.length - 1];
      const lastRow = lastDay?.rows[lastDay.rows.length - 1];
      return lastRow?.record_id;
    },
    enabled: grouping === "day",
  });

  const days = useMemo<DmarcDayGroup[]>(() => {
    const byDate = new Map<string, DmarcDayGroup>();
    for (const page of daysQuery.data?.pages ?? []) {
      for (const day of page.days) {
        const existing = byDate.get(day.date);
        byDate.set(
          day.date,
          existing
            ? {
                date: day.date,
                report_count: existing.report_count + day.report_count,
                message_count: existing.message_count + day.message_count,
                accepted: existing.accepted + day.accepted,
                quarantined: existing.quarantined + day.quarantined,
                rejected: existing.rejected + day.rejected,
                rows: [...existing.rows, ...day.rows],
              }
            : day
        );
      }
    }
    return Array.from(byDate.values());
  }, [daysQuery.data]);

  const groupedQuery = useQuery({
    queryKey: ["dmarc-reports-grouped", domainId, grouping, filterQS],
    queryFn: () =>
      api.get<DmarcReportsGroupedRow[]>(`/domains/${domainId}/dmarc/reports/grouped?by=${grouping}${filterQS ? `&${filterQS}` : ""}`),
    enabled: grouping !== "day",
  });

  const detailQuery = useQuery({
    queryKey: ["dmarc-record-detail", domainId, expandedId],
    queryFn: () => api.get<DmarcRecordDetail>(`/domains/${domainId}/dmarc/records/${expandedId}`),
    enabled: expandedId !== null,
  });

  return (
    <section>
      <div className="page-header">
        <div>
          <h2>DMARC aggregate reports</h2>
        </div>
      </div>

      <FilterBar filters={filters} grouping={grouping} onFilterChange={setFilter} onGroupingChange={(g) => setFilter("group", g)} />

      <SummaryBar summary={summaryQuery.data} isLoading={summaryQuery.isLoading} />

      {grouping === "day" ? (
        <>
          {daysQuery.isLoading && <p className="muted">Loading…</p>}
          {daysQuery.isSuccess && days.length === 0 && <p className="empty-state">No reports match these filters.</p>}

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
                  {day.report_count} report{day.report_count === 1 ? "" : "s"} · {day.message_count} message
                  {day.message_count === 1 ? "" : "s"} · {day.accepted} accepted · {day.quarantined} quarantined · {day.rejected} rejected
                </span>
              </div>
              {day.rows.map((row) => {
                const role = rowRole(row);
                return (
                  <div
                    key={row.record_id}
                    style={{
                      borderBottom: "1px solid var(--border)",
                      borderLeft: role ? `3px solid var(--${role}-text)` : "3px solid transparent",
                      background: role ? `var(--${role}-wash)` : undefined,
                    }}
                  >
                    <div
                      onClick={() => setExpandedId(expandedId === row.record_id ? null : row.record_id)}
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
                        {expandedId === row.record_id ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
                        <strong style={{ fontSize: "0.88rem" }}>{row.org_name}</strong>
                        <span className="muted" style={{ fontSize: "0.82rem" }}>
                          via {row.service_label} (<IpLink ip={row.source_ip} />)
                        </span>
                      </div>
                      <div className="num" style={{ display: "flex", gap: "1rem", alignItems: "center", fontSize: "0.82rem" }}>
                        <span>{row.count}</span>
                        <span style={{ color: resultColor(row.spf_result) }}>SPF {row.spf_result}</span>
                        <span style={{ color: resultColor(row.dkim_result) }}>DKIM {row.dkim_result}</span>
                        <span className={`badge badge--${dispositionRole(row.disposition)}`}>{row.disposition}</span>
                      </div>
                    </div>
                    {expandedId === row.record_id && (
                      <div style={{ padding: "1rem 1.1rem 1.1rem 2.3rem", background: "var(--plane)", borderTop: "1px solid var(--border)" }}>
                        {detailQuery.isLoading && <p className="muted">Loading…</p>}
                        {detailQuery.data && <RecordDetail detail={detailQuery.data} />}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          ))}

          {daysQuery.hasNextPage && (
            <button className="btn btn--secondary" onClick={() => daysQuery.fetchNextPage()} disabled={daysQuery.isFetchingNextPage}>
              {daysQuery.isFetchingNextPage ? "Loading…" : "Load older"}
            </button>
          )}
        </>
      ) : (
        <GroupedTable grouping={grouping} rows={groupedQuery.data} isLoading={groupedQuery.isLoading} />
      )}
    </section>
  );
}

function FilterBar({
  filters,
  grouping,
  onFilterChange,
  onGroupingChange,
}: {
  filters: ReportsFilters;
  grouping: Grouping;
  onFilterChange: (key: string, value: string) => void;
  onGroupingChange: (grouping: Grouping) => void;
}) {
  const [reporter, setReporter] = useState(filters.reporter ?? "");
  const [sourceIp, setSourceIp] = useState(filters.source_ip ?? "");

  useEffect(() => {
    setReporter(filters.reporter ?? "");
    setSourceIp(filters.source_ip ?? "");
  }, [filters.reporter, filters.source_ip]);

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
        <select className="input" value={filters.disposition ?? ""} onChange={(e) => onFilterChange("disposition", e.target.value)}>
          <option value="">Any disposition</option>
          <option value="none">Accepted</option>
          <option value="quarantine">Quarantined</option>
          <option value="reject">Rejected</option>
        </select>
        <select className="input" value={filters.spf_result ?? ""} onChange={(e) => onFilterChange("spf_result", e.target.value)}>
          <option value="">Any SPF result</option>
          <option value="pass">SPF pass</option>
          <option value="fail">SPF fail</option>
        </select>
        <select className="input" value={filters.dkim_result ?? ""} onChange={(e) => onFilterChange("dkim_result", e.target.value)}>
          <option value="">Any DKIM result</option>
          <option value="pass">DKIM pass</option>
          <option value="fail">DKIM fail</option>
        </select>
      </div>
      <form
        className="field-row"
        style={{ marginBottom: "0.6rem" }}
        onSubmit={(e) => {
          e.preventDefault();
          onFilterChange("reporter", reporter);
          onFilterChange("source_ip", sourceIp);
        }}
      >
        <input
          className="input"
          placeholder="Filter by reporter (e.g. google)"
          value={reporter}
          onChange={(e) => setReporter(e.target.value)}
        />
        <input
          className="input"
          placeholder="Filter by source IP"
          value={sourceIp}
          onChange={(e) => setSourceIp(e.target.value)}
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

function SummaryBar({ summary, isLoading }: { summary: DmarcReportsSummary | undefined; isLoading: boolean }) {
  if (isLoading) return <p className="muted">Loading summary…</p>;
  if (!summary || summary.total_reports === 0) return null;

  return (
    <div className="card">
      <div className="stat-row">
        <Stat label="Reports" value={summary.total_reports.toLocaleString()} />
        <Stat label="Messages" value={summary.total_messages.toLocaleString()} />
        <Stat label="Accepted" value={summary.accepted.toLocaleString()} />
        <Stat label="Quarantined" value={summary.quarantined.toLocaleString()} />
        <Stat label="Rejected" value={summary.rejected.toLocaleString()} />
        <Stat label="DMARC pass rate" value={summary.dmarc_pass_pct !== null ? `${summary.dmarc_pass_pct}%` : "—"} />
      </div>
      <div className="chip-row" style={{ marginTop: "0.6rem" }}>
        {summary.top_failing_source && (
          <span className="muted" style={{ fontSize: "0.82rem" }}>
            Top failing source: <strong>{summary.top_failing_source.service_label}</strong> (
            {summary.top_failing_source.failed_count.toLocaleString()} failed)
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

function GroupedTable({
  grouping,
  rows,
  isLoading,
}: {
  grouping: Grouping;
  rows: DmarcReportsGroupedRow[] | undefined;
  isLoading: boolean;
}) {
  const columnLabel = grouping === "source" ? "Source" : grouping === "reporter" ? "Reporter" : "Disposition";

  return (
    <div className="card">
      {isLoading && <p className="muted">Loading…</p>}
      {!isLoading && (rows ?? []).length === 0 && <p className="empty-state">No reports match these filters.</p>}
      {!isLoading && (rows ?? []).length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>{columnLabel}</th>
                <th>Messages</th>
                <th>Reports</th>
                <th>Accepted</th>
                <th>Quarantined</th>
                <th>Rejected</th>
                <th>DMARC pass rate</th>
              </tr>
            </thead>
            <tbody>
              {rows!.map((row) => (
                <tr key={row.key}>
                  <td>
                    {grouping === "disposition" ? <span className={`badge badge--${dispositionRole(row.key)}`}>{row.label}</span> : row.label}
                    {grouping === "source" && row.label !== row.key && (
                      <span className="muted" style={{ marginLeft: "0.4rem", fontSize: "0.8rem" }}>
                        <IpLink ip={row.key} />
                      </span>
                    )}
                  </td>
                  <td className="num">{row.message_count.toLocaleString()}</td>
                  <td className="num">{row.report_count.toLocaleString()}</td>
                  <td className="num">{row.accepted.toLocaleString()}</td>
                  <td className="num">{row.quarantined.toLocaleString()}</td>
                  <td className="num">{row.rejected.toLocaleString()}</td>
                  <td className="num">{row.dmarc_pass_pct !== null ? `${row.dmarc_pass_pct}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function RecordDetail({ detail }: { detail: DmarcRecordDetail }) {
  return (
    <div style={{ fontSize: "0.87rem", display: "grid", gap: "1rem" }}>
      <section>
        <div className="section-title" style={{ fontSize: "0.82rem", textTransform: "uppercase", letterSpacing: "0.03em", color: "var(--ink-muted)" }}>
          Report details
        </div>
        <div className="secondary-text">
          <div>Report ID: {detail.report.report_id}</div>
          <div>Reported by: {detail.report.org_name}</div>
          <div>
            Period: {new Date(detail.report.date_range_begin).toUTCString()} &ndash; {new Date(detail.report.date_range_end).toUTCString()}
          </div>
          {detail.report.policy_p && (
            <div>
              Policy: p={detail.report.policy_p}
              {detail.report.policy_sp && ` sp=${detail.report.policy_sp}`}
              {detail.report.policy_pct !== null && ` pct=${detail.report.policy_pct}`}
            </div>
          )}
        </div>
      </section>

      <section>
        <div className="section-title" style={{ fontSize: "0.82rem", textTransform: "uppercase", letterSpacing: "0.03em", color: "var(--ink-muted)" }}>
          Identifiers
        </div>
        <div className="secondary-text">
          <div>Header From: {detail.header_from}</div>
          <div>Envelope From: {detail.envelope_from ?? "(mail-from address not reported)"}</div>
          <div>Envelope To: {detail.envelope_to ?? "(not reported)"}</div>
        </div>
      </section>

      <section>
        <div className="section-title" style={{ fontSize: "0.82rem", textTransform: "uppercase", letterSpacing: "0.03em", color: "var(--ink-muted)" }}>
          Authentication
        </div>
        <ul style={{ margin: "0.3rem 0 0 1.2rem", padding: 0 }} className="secondary-text">
          {detail.spf_narrative.map((n, i) => (
            <li key={`spf-${i}`} style={{ marginBottom: "0.2rem" }}>
              {n}
            </li>
          ))}
          {detail.dkim_narrative.map((n, i) => (
            <li key={`dkim-${i}`} style={{ marginBottom: "0.2rem" }}>
              {n}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <div className="section-title" style={{ fontSize: "0.82rem", textTransform: "uppercase", letterSpacing: "0.03em", color: "var(--ink-muted)" }}>
          Verdict
        </div>
        <div className="secondary-text">
          <div>SPF aligned: {detail.verdict.spf_aligned ? "yes" : "no"}</div>
          <div>DKIM aligned: {detail.verdict.dkim_aligned ? "yes" : "no"}</div>
          <div style={{ fontWeight: 600, color: "var(--ink-primary)" }}>DMARC aligned: {detail.verdict.dmarc_aligned ? "yes" : "no"}</div>
          <div>
            Disposition applied:{" "}
            <span className={`badge badge--${dispositionRole(detail.verdict.disposition_applied)}`}>{detail.verdict.disposition_applied}</span>
          </div>
        </div>
      </section>

      <section>
        <div className="section-title" style={{ fontSize: "0.82rem", textTransform: "uppercase", letterSpacing: "0.03em", color: "var(--ink-muted)" }}>
          Hosts
        </div>
        <div className="secondary-text">
          <IpLink ip={detail.source_ip} />, {detail.count} email{detail.count === 1 ? "" : "s"}
        </div>
      </section>
    </div>
  );
}
