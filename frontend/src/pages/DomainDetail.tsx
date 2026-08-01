import { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, RefreshCw, Trash2, Plus, ArrowRight } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { Domain } from "../api/types";
import type { DmarcOutboundService, DmarcSummary, DomainRating, InboundHostRow } from "../api/dmarc";
import type { CheckResult, CheckStatus, CheckType, DkimSelectorItem } from "../api/dnsChecks";
import { useAuth } from "../auth/AuthContext";
import { Stat, StatusBadge } from "../components/domain/shared";
import OutboundTable from "../components/domain/OutboundTable";
import InboundTable from "../components/domain/InboundTable";
import DomainRatingCard from "../components/domain/DomainRatingCard";

const CHECK_LABELS: Record<CheckType, string> = {
  spf: "SPF",
  dkim: "DKIM",
  dmarc: "DMARC",
  dmarcbis: "DMARCbis",
  mta_sts: "MTA-STS",
  tls_rpt: "TLS-RPT",
  dane: "DANE",
  mx: "MX",
  starttls: "STARTTLS",
};

const CHECK_ORDER: CheckType[] = ["spf", "dkim", "dmarc", "mx", "starttls", "mta_sts", "dane", "dmarcbis", "tls_rpt"];

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
    queryFn: () => api.get<DmarcOutboundService[]>(`/domains/${domainId}/dmarc/sources`),
  });

  const { data: inboundHosts } = useQuery({
    queryKey: ["dmarc-inbound", domainId],
    queryFn: () => api.get<InboundHostRow[]>(`/domains/${domainId}/dmarc/inbound`),
    enabled: domain?.verification_status === "verified",
  });

  const { data: rating } = useQuery({
    queryKey: ["domain-rating", domainId],
    queryFn: () => api.get<DomainRating>(`/domains/${domainId}/rating`),
  });

  const passRate =
    summary && summary.total_message_count > 0
      ? Math.round((summary.dmarc_pass_count / summary.total_message_count) * 100)
      : null;

  if (!domainId) return null;

  return (
    <section>
      <Link to="/" className="back-link">
        <ArrowLeft size={14} />
        Domains
      </Link>
      <div className="page-header">
        <h1>{domain?.name ?? "…"}</h1>
      </div>

      <DomainRatingCard rating={rating} />

      {domain && <BestPractices domainId={domainId} domain={domain} canManage={canManage} />}

      <h3 className="section-title">DMARC report summary</h3>
      {summaryLoading && <p className="muted">Loading…</p>}
      {summary && summary.report_count === 0 && <p className="empty-state">No aggregate reports received yet for this domain.</p>}
      {summary && summary.report_count > 0 && (
        <div className="stat-row">
          <Stat label="Reports" value={summary.report_count} />
          <Stat label="Total messages" value={summary.total_message_count} />
          <Stat label="DMARC pass rate" value={passRate !== null ? `${passRate}%` : "—"} />
          <Stat label="Failing" value={summary.dmarc_fail_count} />
        </div>
      )}

      {summary && summary.report_count > 0 && (
        <div className="card">
          <h3 className="section-title">Outbound email</h3>
          <p className="section-hint">Services sending email as this domain, per aggregate reports received.</p>
          <OutboundTable services={sources ?? []} />
        </div>
      )}

      {domain?.verification_status === "verified" && (
        <div className="card">
          <h3 className="section-title">Inbound email</h3>
          <p className="section-hint">Hosts that process email for this domain, and whether they enforce TLS.</p>
          <InboundTable hosts={inboundHosts ?? []} />
        </div>
      )}

      {summary && summary.report_count > 0 && (
        <p>
          <Link to={`/domains/${domainId}/reports`} style={{ display: "inline-flex", alignItems: "center", gap: "0.3rem" }}>
            View all reports
            <ArrowRight size={14} />
          </Link>
        </p>
      )}
    </section>
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
    <div className="card">
      <div className="card-header">
        <h3 className="section-title" style={{ margin: 0 }}>
          Best practices
        </h3>
        {canManage && domain.verification_status === "verified" && (
          <button className="btn btn--secondary btn--sm" onClick={() => recheck.mutate()} disabled={recheck.isPending}>
            <RefreshCw />
            {recheck.isPending ? "Checking…" : "Recheck now"}
          </button>
        )}
      </div>

      {domain.verification_status !== "verified" && (
        <div className="alert alert--warning" style={{ marginBottom: 0 }}>
          Verify this domain first — checks won't run until ownership is confirmed.
        </div>
      )}
      {error && (
        <div className="alert alert--critical" style={{ marginBottom: 0 }}>
          {error}
        </div>
      )}
      {isLoading && <p className="muted">Loading…</p>}
      {domain.verification_status === "verified" && !isLoading && (checks ?? []).length === 0 && (
        <p className="empty-state">No checks have run yet — click "Recheck now".</p>
      )}

      {CHECK_ORDER.filter((t) => byType.has(t)).map((checkType) => {
        const results = byType.get(checkType)!;
        return (
          <div key={checkType} style={{ marginTop: "0.9rem" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
              <StatusBadge status={worstStatus(results)} />
              <strong style={{ fontSize: "0.88rem" }}>{CHECK_LABELS[checkType]}</strong>
            </div>
            <ul style={{ margin: "0.4rem 0 0 1.4rem", padding: 0 }}>
              {results.map((r) => (
                <li key={r.id} style={{ fontSize: "0.85rem", marginBottom: "0.3rem", color: "var(--ink-secondary)" }}>
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
    <div style={{ marginTop: "1.25rem", paddingTop: "1rem", borderTop: "1px solid var(--border)" }}>
      <strong style={{ fontSize: "0.88rem" }}>DKIM selectors</strong>
      <p className="section-hint">
        DKIM selectors aren't discoverable via DNS — add the ones this domain actually signs with (check a received
        email's DKIM-Signature header for "s=", or your mail provider's docs).
      </p>
      <div className="chip-row" style={{ marginBottom: canManage ? "0.75rem" : 0 }}>
        {(selectorList ?? []).map((s) => (
          <span key={s.id} className="badge badge--neutral" style={{ gap: "0.4rem" }}>
            <code>{s.selector}</code>
            {canManage && (
              <button
                onClick={() => deleteSelector.mutate(s.id)}
                style={{ background: "none", border: "none", cursor: "pointer", color: "inherit", padding: 0, display: "flex" }}
                title="Remove"
              >
                <Trash2 size={12} />
              </button>
            )}
          </span>
        ))}
        {(selectorList ?? []).length === 0 && <span className="muted" style={{ fontSize: "0.85rem" }}>None added yet.</span>}
      </div>
      {canManage && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            addSelector.mutate();
          }}
          className="field-row"
        >
          <input className="input" value={selector} onChange={(e) => setSelector(e.target.value)} placeholder="selector1" required />
          <button type="submit" className="btn btn--secondary btn--sm" disabled={addSelector.isPending}>
            <Plus />
            Add selector
          </button>
        </form>
      )}
      {error && (
        <div className="alert alert--critical" style={{ marginTop: "0.5rem", marginBottom: 0 }}>
          {error}
        </div>
      )}
    </div>
  );
}
