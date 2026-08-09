import { useState } from "react";
import { useOutletContext } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Trash2, Plus, Wand2, ChevronDown, ChevronRight, Copy, Check } from "lucide-react";
import { api, ApiError } from "../../api/client";
import type { Domain } from "../../api/types";
import type { CheckResult, CheckStatus, CheckType, DetectedSelector, DkimSelectorItem } from "../../api/dnsChecks";
import type { MailboxConnectionStatus } from "../../api/dmarc";
import { useAuth } from "../../auth/AuthContext";
import { StatusBadge } from "../../components/domain/shared";
import PolicyBuilder from "../../components/policy-builder/PolicyBuilder";
import MtaStsPolicyBuilder from "../../components/policy-builder/MtaStsPolicyBuilder";

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

// "Readiness" (DMARCbis) is deliberately separate from Authentication/Inbound
// security — see quiet-row handling below, where it collapses to a single
// line unless there's an actual DMARCbis-specific issue to flag.
const CHECK_CATEGORIES: { label: string; types: CheckType[] }[] = [
  { label: "Authentication", types: ["spf", "dkim", "dmarc"] },
  { label: "Inbound security", types: ["mx", "starttls", "mta_sts", "dane", "tls_rpt"] },
  { label: "Readiness", types: ["dmarcbis"] },
];

const SEVERITY_RANK: Record<CheckStatus, number> = { fail: 0, error: 1, warn: 2, pass: 3 };

function recommendationOf(r: CheckResult): string | null {
  const rec = (r.details as { recommendation?: unknown } | null)?.recommendation;
  return typeof rec === "string" ? rec : null;
}

function worstStatus(results: CheckResult[]): CheckStatus {
  return [...results].sort((a, b) => SEVERITY_RANK[a.status] - SEVERITY_RANK[b.status])[0].status;
}

// The one finding surfaced at a glance for a check type that has several
// (SPF alone can have 4+: record-missing, lookup-count, void-lookups, the
// "all" qualifier) — worst status first, and among same-severity findings
// prefer the one that actually has a recommendation attached.
function primaryFinding(results: CheckResult[]): CheckResult {
  return [...results].sort((a, b) => {
    const sev = SEVERITY_RANK[a.status] - SEVERITY_RANK[b.status];
    if (sev !== 0) return sev;
    return (recommendationOf(b) ? 1 : 0) - (recommendationOf(a) ? 1 : 0);
  })[0];
}

type StatusFilter = "attention" | "all" | "pass";
const STATUS_FILTERS: { key: StatusFilter; label: string }[] = [
  { key: "attention", label: "Needs attention" },
  { key: "all", label: "All" },
  { key: "pass", label: "Passing" },
];

function matchesFilter(status: CheckStatus, filter: StatusFilter): boolean {
  if (filter === "all") return true;
  if (filter === "pass") return status === "pass";
  return status !== "pass";
}

function timeAgo(iso: string): string {
  const hours = (Date.now() - new Date(iso).getTime()) / 3_600_000;
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))}m ago`;
  if (hours < 48) return `${Math.round(hours)}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

export default function DnsChecksTab() {
  const domain = useOutletContext<Domain>();
  const { user } = useAuth();
  const canManage = user?.role === "org_admin";
  const domainId = domain.id;

  const queryClient = useQueryClient();
  const { data: checks, isLoading } = useQuery({
    queryKey: ["dns-checks", domainId],
    queryFn: () => api.get<CheckResult[]>(`/domains/${domainId}/checks`),
  });
  const { data: connection } = useQuery({
    queryKey: ["mailbox-connection"],
    queryFn: () => api.get<MailboxConnectionStatus>("/mailbox-connection"),
    retry: false,
  });
  const [error, setError] = useState<string | null>(null);
  const [showPolicyBuilder, setShowPolicyBuilder] = useState(false);
  const [showMtaStsBuilder, setShowMtaStsBuilder] = useState(false);
  const [filter, setFilter] = useState<StatusFilter>("attention");

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

  // Counted per check TYPE (worst finding wins), not per raw finding — a
  // domain with 9 check types and a couple of multi-finding checks would
  // otherwise report a confusing "17 pass, 6 warn" instead of one number
  // per thing a user actually thinks of as "a check."
  const typeStatuses = CHECK_ORDER.filter((t) => byType.has(t)).map((t) => worstStatus(byType.get(t)!));
  const summaryCounts = {
    pass: typeStatuses.filter((s) => s === "pass").length,
    warn: typeStatuses.filter((s) => s === "warn").length,
    fail: typeStatuses.filter((s) => s === "fail" || s === "error").length,
  };
  const lastChecked = (checks ?? []).reduce<string | null>((latest, c) => (!latest || c.checked_at > latest ? c.checked_at : latest), null);

  return (
    <div className="card">
      <div className="card-header">
        <h3 className="section-title" style={{ margin: 0 }}>
          DNS Checks
        </h3>
        <div className="chip-row">
          {canManage && (
            <button className="btn btn--ghost btn--sm" onClick={() => setShowPolicyBuilder(true)}>
              <Wand2 />
              Build DMARC policy
            </button>
          )}
          {canManage && (
            <button className="btn btn--ghost btn--sm" onClick={() => setShowMtaStsBuilder(true)}>
              <Wand2 />
              Build MTA-STS policy
            </button>
          )}
          {canManage && domain.verification_status === "verified" && (
            <button className="btn btn--secondary btn--sm" onClick={() => recheck.mutate()} disabled={recheck.isPending}>
              <RefreshCw />
              {recheck.isPending ? "Checking…" : "Recheck now"}
            </button>
          )}
        </div>
      </div>

      {showPolicyBuilder && (
        <PolicyBuilder domainId={domainId} domainName={domain.name} onClose={() => setShowPolicyBuilder(false)} />
      )}
      {showMtaStsBuilder && (
        <MtaStsPolicyBuilder domainId={domainId} domainName={domain.name} onClose={() => setShowMtaStsBuilder(false)} />
      )}

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

      {!isLoading && (checks ?? []).length > 0 && (
        <>
          <div className="chip-row" style={{ marginBottom: "0.9rem" }}>
            <span className={`badge ${summaryCounts.fail > 0 ? "badge--critical" : "badge--neutral"}`}>{summaryCounts.fail} fail</span>
            <span className={`badge ${summaryCounts.warn > 0 ? "badge--warning" : "badge--neutral"}`}>{summaryCounts.warn} warn</span>
            <span className="badge badge--good">{summaryCounts.pass} pass</span>
            {lastChecked && <span className="muted" style={{ fontSize: "0.82rem" }}>Last checked {timeAgo(lastChecked)}</span>}
          </div>

          <div className="chip-row" style={{ marginBottom: "1.1rem" }}>
            {STATUS_FILTERS.map((f) => (
              <button
                key={f.key}
                className="btn btn--ghost btn--sm"
                style={filter === f.key ? { background: "var(--accent-wash)", color: "var(--accent)" } : undefined}
                onClick={() => setFilter(f.key)}
              >
                {f.label}
              </button>
            ))}
          </div>

          {CHECK_CATEGORIES.map((category) => {
            const typesPresent = category.types.filter((t) => byType.has(t));
            if (typesPresent.length === 0) return null;

            // Readiness (DMARCbis) collapses to a quiet single line unless
            // there's an actual issue — otherwise it competes for attention
            // with real SPF/DKIM/MTA-STS warnings despite usually having
            // nothing to say.
            if (category.label === "Readiness") {
              const results = typesPresent.flatMap((t) => byType.get(t)!);
              const status = worstStatus(results);
              if (status === "pass") {
                if (filter === "pass" || filter === "all") {
                  return (
                    <div key={category.label} className="muted" style={{ fontSize: "0.8rem", marginTop: "0.6rem" }}>
                      DMARCbis (RFC 9989) readiness: no issues to flag.
                    </div>
                  );
                }
                return null;
              }
            }

            const groupStatuses = typesPresent.map((t) => worstStatus(byType.get(t)!));
            const groupWarn = groupStatuses.filter((s) => s !== "pass").length;
            const groupPass = groupStatuses.filter((s) => s === "pass").length;
            const visibleTypes = typesPresent.filter((t) => matchesFilter(worstStatus(byType.get(t)!), filter));
            if (visibleTypes.length === 0) return null;

            return (
              <div key={category.label} style={{ marginTop: "1.3rem" }}>
                <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between" }}>
                  <strong style={{ fontSize: "0.95rem" }}>{category.label}</strong>
                  <span className="muted" style={{ fontSize: "0.78rem" }}>
                    {groupWarn > 0 ? `${groupWarn} to review · ` : ""}
                    {groupPass} passing
                  </span>
                </div>
                <div style={{ marginTop: "0.6rem", display: "flex", flexDirection: "column", gap: "0.5rem" }}>
                  {CHECK_ORDER.filter((t) => visibleTypes.includes(t)).map((checkType) => (
                    <CheckRow
                      key={checkType}
                      checkType={checkType}
                      results={byType.get(checkType)!}
                      domainId={domainId}
                      domainName={domain.name}
                      canManage={canManage}
                      orgMailboxAddress={connection?.mailbox_address ?? null}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}

function CheckRow({
  checkType,
  results,
  domainId,
  domainName,
  canManage,
  orgMailboxAddress,
}: {
  checkType: CheckType;
  results: CheckResult[];
  domainId: string;
  domainName: string;
  canManage: boolean;
  orgMailboxAddress: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const status = worstStatus(results);
  const primary = primaryFinding(results);
  const recommendation = recommendationOf(primary);
  // DKIM's expanded area always holds selector management, and MTA-STS/
  // TLS-RPT's holds the suggested-record block when the record is missing
  // — both need to stay reachable even when the single finding shown as
  // "primary" has no extra evidence of its own to expand into.
  const hasMoreEvidence =
    results.length > 1 ||
    !!primary.subject ||
    Object.keys(primary.details ?? {}).length > (recommendation ? 1 : 0) ||
    checkType === "dkim" ||
    checkType === "mta_sts" ||
    checkType === "tls_rpt";

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderLeft: `3px solid var(--${status === "pass" ? "good" : status === "warn" ? "warning" : "critical"}-text)`,
        borderRadius: "var(--radius-sm)",
        padding: "0.65rem 0.85rem",
      }}
    >
      <div
        style={{ display: "flex", alignItems: "flex-start", gap: "0.6rem", cursor: hasMoreEvidence ? "pointer" : "default" }}
        onClick={() => hasMoreEvidence && setExpanded((v) => !v)}
      >
        <StatusBadge status={status} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: "0.5rem", flexWrap: "wrap" }}>
            <strong style={{ fontSize: "0.88rem" }}>{CHECK_LABELS[checkType]}</strong>
            <span style={{ fontSize: "0.85rem", color: "var(--ink-secondary)", wordBreak: "break-all" }}>{primary.summary}</span>
          </div>
          {recommendation && (
            <div style={{ fontSize: "0.82rem", color: "var(--ink-secondary)", marginTop: "0.2rem", wordBreak: "break-all" }}>
              {recommendation}
            </div>
          )}
        </div>
        {hasMoreEvidence && (
          <span style={{ flexShrink: 0, color: "var(--ink-muted)" }}>{expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}</span>
        )}
      </div>

      {expanded && (
        <div style={{ marginTop: "0.6rem", paddingTop: "0.6rem", borderTop: "1px solid var(--border)" }}>
          <ul style={{ margin: 0, padding: "0 0 0 1.1rem", fontSize: "0.82rem", color: "var(--ink-secondary)", wordBreak: "break-all" }}>
            {results.map((r) => (
              <li key={r.id} style={{ marginBottom: "0.3rem" }}>
                <StatusBadge status={r.status} /> {r.subject && <code style={{ marginRight: "0.3rem" }}>{r.subject}</code>}
                {r.summary}
              </li>
            ))}
          </ul>

          {(checkType === "mta_sts" || checkType === "tls_rpt") && (
            <SuggestedRecord checkType={checkType} domainName={domainName} finding={primary} orgMailboxAddress={orgMailboxAddress} />
          )}

          {checkType === "dkim" && <DkimSelectors domainId={domainId} canManage={canManage} />}
        </div>
      )}
    </div>
  );
}

// Builds a copy-able DNS record for the "record entirely missing" case —
// MTA-STS and TLS-RPT are the two checks where that's both common and has
// an unambiguous fix, and TLS-RPT's rua= can reuse the org's already
// connected mailbox instead of a generic placeholder.
function SuggestedRecord({
  checkType,
  domainName,
  finding,
  orgMailboxAddress,
}: {
  checkType: "mta_sts" | "tls_rpt";
  domainName: string;
  finding: CheckResult;
  orgMailboxAddress: string | null;
}) {
  const [copied, setCopied] = useState(false);
  const isMissing = checkType === "mta_sts" ? finding.summary === "No MTA-STS record found" : finding.summary === "No TLS-RPT record found";
  if (!isMissing) return null;

  const host = checkType === "mta_sts" ? `_mta-sts.${domainName}` : `_smtp._tls.${domainName}`;
  const value =
    checkType === "mta_sts"
      ? `v=STSv1; id=${new Date().toISOString().replace(/[-:T.]/g, "").slice(0, 12)}`
      : `v=TLSRPTv1; rua=mailto:${orgMailboxAddress ?? "your-reports-address@yourdomain"}`;

  function copy() {
    navigator.clipboard.writeText(value).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div style={{ marginTop: "0.6rem", paddingTop: "0.6rem", borderTop: "1px solid var(--border)" }}>
      <div className="stat-tile-label" style={{ marginBottom: "0.3rem" }}>
        Recommended DNS
      </div>
      <div style={{ fontFamily: "monospace", fontSize: "0.8rem", color: "var(--ink-secondary)" }}>
        Host: <strong>{host}</strong>
        <br />
        Type: <strong>TXT</strong>
        <br />
        Value: <strong>{value}</strong>
      </div>
      <button className="btn btn--ghost btn--sm" style={{ marginTop: "0.4rem" }} onClick={copy}>
        {copied ? <Check /> : <Copy />}
        {copied ? "Copied" : "Copy value"}
      </button>
      {checkType === "mta_sts" && (
        <p className="section-hint" style={{ marginTop: "0.4rem", marginBottom: 0 }}>
          Also requires a policy file at <code>https://mta-sts.{domainName}/.well-known/mta-sts.txt</code> — the DNS
          record alone isn't enough.
        </p>
      )}
    </div>
  );
}

function DkimSelectors({ domainId, canManage }: { domainId: string; canManage: boolean }) {
  const queryClient = useQueryClient();
  const { data: selectorList } = useQuery({
    queryKey: ["dkim-selectors", domainId],
    queryFn: () => api.get<DkimSelectorItem[]>(`/domains/${domainId}/selectors`),
  });
  const { data: detectedSelectors } = useQuery({
    queryKey: ["dkim-selectors-detected", domainId],
    queryFn: () => api.get<DetectedSelector[]>(`/domains/${domainId}/selectors/detected`),
  });
  const [selector, setSelector] = useState("");
  const [error, setError] = useState<string | null>(null);

  const addSelector = useMutation({
    mutationFn: (value: string) => api.post<DkimSelectorItem>(`/domains/${domainId}/selectors`, { selector: value }),
    onSuccess: () => {
      setSelector("");
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["dkim-selectors", domainId] });
      queryClient.invalidateQueries({ queryKey: ["dkim-selectors-detected", domainId] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "failed to add selector"),
  });

  const deleteSelector = useMutation({
    mutationFn: (id: string) => api.delete(`/domains/${domainId}/selectors/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["dkim-selectors", domainId] }),
  });

  return (
    <div style={{ marginTop: "0.6rem", paddingTop: "0.6rem", borderTop: "1px solid var(--border)" }}>
      <div className="stat-tile-label" style={{ marginBottom: "0.3rem" }}>
        Selectors
      </div>
      <p className="section-hint" style={{ marginTop: 0, marginBottom: "0.5rem" }}>
        Selectors aren't discoverable via DNS — add the ones this domain actually signs with (check a received
        email's DKIM-Signature header for "s=", or your mail provider's docs).
      </p>
      <div className="chip-row" style={{ marginBottom: canManage ? "0.6rem" : 0 }}>
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
      {(detectedSelectors ?? []).length > 0 && (
        <div style={{ marginBottom: "0.6rem" }}>
          <div className="muted" style={{ fontSize: "0.8rem", marginBottom: "0.3rem" }}>
            Detected in reports, not yet added:
          </div>
          <div className="chip-row">
            {(detectedSelectors ?? []).map((s) => (
              <span key={s.selector} className="badge badge--neutral" style={{ gap: "0.4rem" }}>
                <code>{s.selector}</code>
                <span className="muted" style={{ fontSize: "0.75rem" }}>
                  {s.message_volume} messages
                </span>
                {canManage && (
                  <button
                    onClick={() => addSelector.mutate(s.selector)}
                    disabled={addSelector.isPending}
                    style={{ background: "none", border: "none", cursor: "pointer", color: "inherit", padding: 0, display: "flex" }}
                    title="Add this selector"
                  >
                    <Plus size={12} />
                  </button>
                )}
              </span>
            ))}
          </div>
        </div>
      )}
      {canManage && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            addSelector.mutate(selector);
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
