import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { ArrowLeft, ChevronDown, ChevronRight } from "lucide-react";
import { api } from "../api/client";
import type { Domain } from "../api/types";
import type { DmarcDayGroup, DmarcRecordDetail, DmarcReportsByDay } from "../api/dmarc";

function dispositionRole(disposition: string): "good" | "warning" | "critical" {
  if (disposition === "reject") return "critical";
  if (disposition === "quarantine") return "warning";
  return "good";
}

function resultColor(result: string): string {
  return result === "pass" ? "var(--good-text)" : "var(--critical-text)";
}

export default function DomainReports() {
  const { domainId } = useParams<{ domainId: string }>();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data: domain } = useQuery({
    queryKey: ["domains", domainId],
    queryFn: () => api.get<Domain>(`/domains/${domainId}`),
  });

  const daysQuery = useInfiniteQuery({
    queryKey: ["dmarc-reports-by-day", domainId],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) =>
      api.get<DmarcReportsByDay>(`/domains/${domainId}/dmarc/reports/by-day${pageParam ? `?before_id=${pageParam}` : ""}`),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => {
      if (!lastPage.has_more) return undefined;
      const lastDay = lastPage.days[lastPage.days.length - 1];
      const lastRow = lastDay?.rows[lastDay.rows.length - 1];
      return lastRow?.record_id;
    },
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

  const detailQuery = useQuery({
    queryKey: ["dmarc-record-detail", domainId, expandedId],
    queryFn: () => api.get<DmarcRecordDetail>(`/domains/${domainId}/dmarc/records/${expandedId}`),
    enabled: expandedId !== null,
  });

  if (!domainId) return null;

  return (
    <section>
      <Link to={`/domains/${domainId}`} className="back-link">
        <ArrowLeft size={14} />
        {domain?.name ?? "Domain"}
      </Link>
      <div className="page-header">
        <div>
          <h1>DMARC aggregate reports</h1>
          <p className="page-subtitle">For {domain?.name ?? "…"}</p>
        </div>
      </div>

      {daysQuery.isLoading && <p className="muted">Loading…</p>}
      {daysQuery.isSuccess && days.length === 0 && <p className="empty-state">No aggregate reports received yet for this domain.</p>}

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
          {day.rows.map((row) => (
            <div key={row.record_id} style={{ borderBottom: "1px solid var(--border)" }}>
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
                    about host {row.source_ip}
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
          ))}
        </div>
      ))}

      {daysQuery.hasNextPage && (
        <button className="btn btn--secondary" onClick={() => daysQuery.fetchNextPage()} disabled={daysQuery.isFetchingNextPage}>
          {daysQuery.isFetchingNextPage ? "Loading…" : "Load older"}
        </button>
      )}
    </section>
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
          {detail.source_ip}, {detail.count} email{detail.count === 1 ? "" : "s"}
        </div>
      </section>
    </div>
  );
}
