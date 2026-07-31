import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Domain } from "../api/types";
import type { DmarcDayGroup, DmarcRecordDetail, DmarcReportsByDay } from "../api/dmarc";

function dispositionColor(disposition: string): string {
  if (disposition === "reject") return "#991b1b";
  if (disposition === "quarantine") return "#92400e";
  return "#166534";
}

function resultColor(result: string): string {
  return result === "pass" ? "#166534" : "#991b1b";
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
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 1000, margin: "0 auto" }}>
      <p>
        <Link to={`/domains/${domainId}`}>&larr; {domain?.name ?? "Domain"}</Link>
      </p>
      <h2>DMARC aggregate reports</h2>
      <p style={{ color: "#6b7280" }}>For {domain?.name ?? "…"}</p>

      {daysQuery.isLoading && <p>Loading…</p>}
      {daysQuery.isSuccess && days.length === 0 && (
        <p style={{ color: "#6b7280" }}>No aggregate reports received yet for this domain.</p>
      )}

      {days.map((day) => (
        <div key={day.date} style={{ marginBottom: "1.5rem", border: "1px solid #e5e7eb", borderRadius: 8 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              padding: "0.75rem 1rem",
              background: "#f9fafb",
              borderBottom: "1px solid #e5e7eb",
            }}
          >
            <strong>{new Date(day.date).toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" })}</strong>
            <span style={{ fontSize: "0.85rem", color: "#6b7280" }}>
              {day.report_count} report{day.report_count === 1 ? "" : "s"} · {day.message_count} message
              {day.message_count === 1 ? "" : "s"} · {day.accepted} accepted · {day.quarantined} quarantined ·{" "}
              {day.rejected} rejected
            </span>
          </div>
          {day.rows.map((row) => (
            <div key={row.record_id} style={{ borderBottom: "1px solid #f3f4f6" }}>
              <div
                onClick={() => setExpandedId(expandedId === row.record_id ? null : row.record_id)}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "0.6rem 1rem",
                  cursor: "pointer",
                }}
              >
                <div>
                  <strong>{row.org_name}</strong>
                  <span style={{ color: "#6b7280", fontSize: "0.85rem" }}> about host {row.source_ip}</span>
                </div>
                <div style={{ display: "flex", gap: "1rem", alignItems: "center", fontSize: "0.85rem" }}>
                  <span>{row.count}</span>
                  <span style={{ color: resultColor(row.spf_result) }}>SPF {row.spf_result}</span>
                  <span style={{ color: resultColor(row.dkim_result) }}>DKIM {row.dkim_result}</span>
                  <span style={{ color: dispositionColor(row.disposition), fontWeight: 600 }}>{row.disposition}</span>
                </div>
              </div>
              {expandedId === row.record_id && (
                <div style={{ padding: "1rem", background: "#fafafa", borderTop: "1px solid #f3f4f6" }}>
                  {detailQuery.isLoading && <p>Loading…</p>}
                  {detailQuery.data && <RecordDetail detail={detailQuery.data} />}
                </div>
              )}
            </div>
          ))}
        </div>
      ))}

      {daysQuery.hasNextPage && (
        <button onClick={() => daysQuery.fetchNextPage()} disabled={daysQuery.isFetchingNextPage}>
          {daysQuery.isFetchingNextPage ? "Loading…" : "Load older"}
        </button>
      )}
    </main>
  );
}

function RecordDetail({ detail }: { detail: DmarcRecordDetail }) {
  return (
    <div style={{ fontSize: "0.9rem" }}>
      <section style={{ marginBottom: "1rem" }}>
        <strong>Report details</strong>
        <div style={{ color: "#4b5563", marginTop: "0.25rem" }}>
          <div>Report ID: {detail.report.report_id}</div>
          <div>Reported by: {detail.report.org_name}</div>
          <div>
            Period: {new Date(detail.report.date_range_begin).toUTCString()} &ndash;{" "}
            {new Date(detail.report.date_range_end).toUTCString()}
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

      <section style={{ marginBottom: "1rem" }}>
        <strong>Identifiers</strong>
        <div style={{ color: "#4b5563", marginTop: "0.25rem" }}>
          <div>Header From: {detail.header_from}</div>
          <div>Envelope From: {detail.envelope_from ?? "(mail-from address not reported)"}</div>
          <div>Envelope To: {detail.envelope_to ?? "(not reported)"}</div>
        </div>
      </section>

      <section style={{ marginBottom: "1rem" }}>
        <strong>Authentication</strong>
        <ul style={{ margin: "0.25rem 0 0 1.25rem", color: "#4b5563" }}>
          {detail.spf_narrative.map((n, i) => (
            <li key={`spf-${i}`}>{n}</li>
          ))}
          {detail.dkim_narrative.map((n, i) => (
            <li key={`dkim-${i}`}>{n}</li>
          ))}
        </ul>
      </section>

      <section style={{ marginBottom: "1rem" }}>
        <strong>Verdict</strong>
        <div style={{ color: "#4b5563", marginTop: "0.25rem" }}>
          <div>SPF aligned: {detail.verdict.spf_aligned ? "yes" : "no"}</div>
          <div>DKIM aligned: {detail.verdict.dkim_aligned ? "yes" : "no"}</div>
          <div style={{ fontWeight: 600 }}>DMARC aligned: {detail.verdict.dmarc_aligned ? "yes" : "no"}</div>
          <div>
            Disposition applied: <span style={{ color: dispositionColor(detail.verdict.disposition_applied) }}>{detail.verdict.disposition_applied}</span>
          </div>
        </div>
      </section>

      <section>
        <strong>Hosts</strong>
        <div style={{ color: "#4b5563", marginTop: "0.25rem" }}>
          {detail.source_ip}, {detail.count} email{detail.count === 1 ? "" : "s"}
        </div>
      </section>
    </div>
  );
}
