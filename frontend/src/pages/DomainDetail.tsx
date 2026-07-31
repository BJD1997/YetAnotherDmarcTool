import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { Domain } from "../api/types";
import type { DmarcReportListItem, DmarcSource, DmarcSummary } from "../api/dmarc";
import type { CheckResult, CheckStatus, CheckType, DkimSelectorItem } from "../api/dnsChecks";
import { useAuth } from "../auth/AuthContext";

const CHECK_LABELS: Record<CheckType, string> = {
  spf: "SPF",
  dkim: "DKIM",
  dmarc: "DMARC",
  dmarcbis: "DMARCbis",
  mta_sts: "MTA-STS",
  tls_rpt: "TLS-RPT",
  dane: "DANE",
  mx: "MX",
};

const CHECK_ORDER: CheckType[] = ["spf", "dkim", "dmarc", "mx", "mta_sts", "dane", "dmarcbis", "tls_rpt"];

export default function DomainDetail() {
  const { domainId } = useParams<{ domainId: string }>();
  const { user } = useAuth();
  const canManage = user?.role === "org_admin";

  const { data: domain } = useQuery({
    queryKey: ["domains", domainId],
    queryFn: () => api.get<Domain>(`/domains/${domainId}`),
  });

  const { data: summary, isLoading: summaryLoading } = useQuery({
    queryKey: ["dmarc-summary", domainId],
    queryFn: () => api.get<DmarcSummary>(`/domains/${domainId}/dmarc/summary`),
  });

  const { data: sources } = useQuery({
    queryKey: ["dmarc-sources", domainId],
    queryFn: () => api.get<DmarcSource[]>(`/domains/${domainId}/dmarc/sources`),
  });

  const { data: reports } = useQuery({
    queryKey: ["dmarc-reports-list", domainId],
    queryFn: () => api.get<DmarcReportListItem[]>(`/domains/${domainId}/dmarc/reports`),
  });

  const passRate =
    summary && summary.total_message_count > 0
      ? Math.round((summary.dmarc_pass_count / summary.total_message_count) * 100)
      : null;

  if (!domainId) return null;

  return (
    <section>
      <p>
        <Link to="/">&larr; Domains</Link>
      </p>
      <h2>{domain?.name ?? "…"}</h2>

      {domain && <BestPractices domainId={domainId} domain={domain} canManage={canManage} />}

      <h3>DMARC report summary</h3>
      {summaryLoading && <p>Loading…</p>}
      {summary && summary.report_count === 0 && (
        <p style={{ color: "#4b5563" }}>No aggregate reports received yet for this domain.</p>
      )}
      {summary && summary.report_count > 0 && (
        <div style={{ display: "flex", gap: "2rem", margin: "1rem 0" }}>
          <Stat label="Reports" value={summary.report_count} />
          <Stat label="Total messages" value={summary.total_message_count} />
          <Stat label="DMARC pass rate" value={passRate !== null ? `${passRate}%` : "—"} />
          <Stat label="Failing" value={summary.dmarc_fail_count} />
        </div>
      )}

      {sources && sources.length > 0 && (
        <>
          <h3>Top sources</h3>
          <table style={{ borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left" }}>
                <th style={{ paddingRight: "1.5rem" }}>Source IP</th>
                <th style={{ paddingRight: "1.5rem" }}>Header From</th>
                <th>Volume</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.source_ip + s.header_from}>
                  <td style={{ paddingRight: "1.5rem" }}>{s.source_ip}</td>
                  <td style={{ paddingRight: "1.5rem" }}>{s.header_from}</td>
                  <td>{s.count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {reports && reports.length > 0 && (
        <>
          <h3>Recent reports</h3>
          <table style={{ borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ textAlign: "left" }}>
                <th style={{ paddingRight: "1.5rem" }}>Reporter</th>
                <th style={{ paddingRight: "1.5rem" }}>Period</th>
                <th style={{ paddingRight: "1.5rem" }}>Policy</th>
                <th>Received</th>
              </tr>
            </thead>
            <tbody>
              {reports.map((r) => (
                <tr key={r.id}>
                  <td style={{ paddingRight: "1.5rem" }}>{r.org_name}</td>
                  <td style={{ paddingRight: "1.5rem" }}>
                    {new Date(r.date_range_begin).toLocaleDateString()} &ndash;{" "}
                    {new Date(r.date_range_end).toLocaleDateString()}
                  </td>
                  <td style={{ paddingRight: "1.5rem" }}>
                    p={r.policy_p ?? "?"} pct={r.policy_pct ?? "?"}
                  </td>
                  <td>{new Date(r.received_at).toLocaleString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div style={{ fontSize: "1.5rem", fontWeight: 600 }}>{value}</div>
      <div style={{ color: "#6b7280", fontSize: "0.85rem" }}>{label}</div>
    </div>
  );
}

function StatusBadge({ status }: { status: CheckStatus }) {
  const colors: Record<CheckStatus, [string, string]> = {
    pass: ["#dcfce7", "#166534"],
    warn: ["#fef3c7", "#92400e"],
    fail: ["#fee2e2", "#991b1b"],
    error: ["#f3f4f6", "#4b5563"],
  };
  const [background, color] = colors[status];
  return (
    <span
      style={{
        display: "inline-block",
        fontSize: "0.7rem",
        fontWeight: 600,
        textTransform: "uppercase",
        padding: "0.1rem 0.5rem",
        borderRadius: 999,
        background,
        color,
        marginRight: "0.5rem",
      }}
    >
      {status}
    </span>
  );
}

function BestPractices({ domainId, domain, canManage }: { domainId: string; domain: Domain; canManage: boolean }) {
  const queryClient = useQueryClient();
  const { data: checks, isLoading } = useQuery({
    queryKey: ["dns-checks", domainId],
    queryFn: () => api.get<CheckResult[]>(`/domains/${domainId}/checks`),
  });
  const [error, setError] = useState<string | null>(null);

  const recheck = useMutation({
    mutationFn: () => api.post<CheckResult[]>(`/domains/${domainId}/checks/recheck`),
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["dns-checks", domainId] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "recheck failed"),
  });

  const byType = new Map<CheckType, CheckResult[]>();
  for (const c of checks ?? []) {
    const list = byType.get(c.check_type) ?? [];
    list.push(c);
    byType.set(c.check_type, list);
  }

  const worstStatus = (results: CheckResult[]): CheckStatus => {
    if (results.some((r) => r.status === "fail")) return "fail";
    if (results.some((r) => r.status === "error")) return "error";
    if (results.some((r) => r.status === "warn")) return "warn";
    return "pass";
  };

  return (
    <div style={{ margin: "1.5rem 0", padding: "1rem", border: "1px solid #e5e7eb", borderRadius: 8 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ margin: 0 }}>Best practices</h3>
        {canManage && domain.verification_status === "verified" && (
          <button onClick={() => recheck.mutate()} disabled={recheck.isPending}>
            {recheck.isPending ? "Checking…" : "Recheck now"}
          </button>
        )}
      </div>

      {domain.verification_status !== "verified" && (
        <p style={{ color: "#92400e" }}>Verify this domain first — checks won't run until ownership is confirmed.</p>
      )}
      {error && <p style={{ color: "#b91c1c" }}>{error}</p>}
      {isLoading && <p>Loading…</p>}
      {domain.verification_status === "verified" && !isLoading && (checks ?? []).length === 0 && (
        <p style={{ color: "#4b5563" }}>No checks have run yet — click "Recheck now".</p>
      )}

      {CHECK_ORDER.filter((t) => byType.has(t)).map((checkType) => {
        const results = byType.get(checkType)!;
        return (
          <div key={checkType} style={{ marginTop: "0.75rem" }}>
            <StatusBadge status={worstStatus(results)} />
            <strong>{CHECK_LABELS[checkType]}</strong>
            <ul style={{ margin: "0.25rem 0 0 1.5rem" }}>
              {results.map((r) => (
                <li key={r.id} style={{ fontSize: "0.9rem", marginBottom: "0.15rem" }}>
                  <StatusBadge status={r.status} />
                  {r.subject && <code style={{ marginRight: "0.4rem" }}>{r.subject}:</code>}
                  {r.summary}
                </li>
              ))}
            </ul>
          </div>
        );
      })}

      <DkimSelectors domainId={domainId} canManage={canManage} />
    </div>
  );
}

function DkimSelectors({ domainId, canManage }: { domainId: string; canManage: boolean }) {
  const queryClient = useQueryClient();
  const { data: selectorList } = useQuery({
    queryKey: ["dkim-selectors", domainId],
    queryFn: () => api.get<DkimSelectorItem[]>(`/domains/${domainId}/selectors`),
  });
  const [selector, setSelector] = useState("");
  const [error, setError] = useState<string | null>(null);

  const addSelector = useMutation({
    mutationFn: () => api.post<DkimSelectorItem>(`/domains/${domainId}/selectors`, { selector }),
    onSuccess: () => {
      setSelector("");
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["dkim-selectors", domainId] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "failed to add selector"),
  });

  const deleteSelector = useMutation({
    mutationFn: (id: string) => api.delete(`/domains/${domainId}/selectors/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["dkim-selectors", domainId] }),
  });

  return (
    <div style={{ marginTop: "1rem", paddingTop: "0.75rem", borderTop: "1px solid #f3f4f6" }}>
      <strong style={{ fontSize: "0.9rem" }}>DKIM selectors</strong>
      <p style={{ color: "#6b7280", fontSize: "0.85rem", margin: "0.25rem 0" }}>
        DKIM selectors aren't discoverable via DNS — add the ones this domain actually signs with (check a
        received email's DKIM-Signature header for "s=", or your mail provider's docs).
      </p>
      <ul style={{ listStyle: "none", padding: 0, fontSize: "0.9rem" }}>
        {(selectorList ?? []).map((s) => (
          <li key={s.id} style={{ marginBottom: "0.25rem" }}>
            <code>{s.selector}</code>
            {canManage && (
              <button style={{ marginLeft: "0.5rem" }} onClick={() => deleteSelector.mutate(s.id)}>
                Remove
              </button>
            )}
          </li>
        ))}
        {(selectorList ?? []).length === 0 && <li style={{ color: "#6b7280" }}>None added yet.</li>}
      </ul>
      {canManage && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            addSelector.mutate();
          }}
          style={{ display: "flex", gap: "0.5rem", marginTop: "0.5rem" }}
        >
          <input value={selector} onChange={(e) => setSelector(e.target.value)} placeholder="selector1" required />
          <button type="submit" disabled={addSelector.isPending}>
            Add selector
          </button>
        </form>
      )}
      {error && <p style={{ color: "#b91c1c", fontSize: "0.85rem" }}>{error}</p>}
    </div>
  );
}
