import { useState } from "react";
import { Link } from "react-router-dom";
import {
  FileText,
  Users,
  ShieldCheck,
  MoreVertical,
  RefreshCw,
  Archive,
  ArchiveRestore,
  Trash2,
  ChevronDown,
  ChevronRight,
  Settings as SettingsIcon,
} from "lucide-react";
import { ApiError } from "../api/client";
import type { Domain } from "../api/types";
import type { RankedDomain } from "../api/overview";
import { noDataRecommendation } from "../api/overview";
import { useAuth } from "../auth/AuthContext";
import { ReportFreshnessValue } from "../components/overview/widgets";
import { MAIL_PROFILE_LABELS, VerificationBadge } from "../components/domain/shared";
import { useDeleteDomain, useDomains, useRankedDomains, useUpdateDomain, useVerifyDomain } from "../hooks/useDomains";

export default function Domains() {
  const { user } = useAuth();
  const { data: domains, isLoading } = useDomains();
  // Same query key DomainsNeedingAttention already uses on Overview — React
  // Query dedupes the fetch if that page was visited this session.
  const { data: ranked } = useRankedDomains();
  const rankedById = new Map((ranked ?? []).map((r) => [r.domain_id, r]));

  const apexDomains = (domains ?? []).filter((d) => !d.parent_domain_id);
  const subdomainsByParent = new Map<string, Domain[]>();
  for (const d of domains ?? []) {
    if (d.parent_domain_id) {
      const list = subdomainsByParent.get(d.parent_domain_id) ?? [];
      list.push(d);
      subdomainsByParent.set(d.parent_domain_id, list);
    }
  }

  const canManage = user?.role === "org_admin";

  return (
    <section>
      <div className="page-header">
        <div>
          <h1>Domains</h1>
          <p className="page-subtitle">
            A domain must be verified (proof you control its DNS) before any best-practice checks run against it.
          </p>
        </div>
        {canManage && (
          <Link to="/settings/domains" className="btn btn--secondary btn--sm">
            <SettingsIcon />
            Add domain
          </Link>
        )}
      </div>

      {isLoading && <p className="muted">Loading…</p>}
      {!isLoading && apexDomains.length === 0 && (
        <p className="empty-state">
          No domains yet. {canManage && <Link to="/settings/domains">Add one in Settings.</Link>}
        </p>
      )}

      {!isLoading && apexDomains.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Domain</th>
                <th title="Weighted score across DNS checks, policy strength, and DMARC pass rate — not a raw pass rate itself.">
                  Grade
                </th>
                <th>Policy</th>
                <th title="Last 90 days, excluding traffic from senders you've marked blocked — the same population the grade is scored on.">
                  Messages (90d)
                </th>
                <th title="Last 90 days, excluding traffic from senders you've marked blocked — the same population the grade is scored on.">
                  Failed (90d)
                </th>
                <th>Last report</th>
                <th>Checks</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {apexDomains.map((domain) => {
                const subs = subdomainsByParent.get(domain.id) ?? [];
                return (
                  <DomainTreeRows
                    key={domain.id}
                    domain={domain}
                    subdomains={subs}
                    canManage={canManage}
                    ranked={rankedById.get(domain.id)}
                    rankedById={rankedById}
                  />
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function DomainTreeRows({
  domain,
  subdomains,
  canManage,
  ranked,
  rankedById,
}: {
  domain: Domain;
  subdomains: Domain[];
  canManage: boolean;
  ranked: RankedDomain | undefined;
  rankedById: Map<string, RankedDomain>;
}) {
  return (
    <>
      <DomainRow domain={domain} canManage={canManage} ranked={ranked} indent={false} />
      {subdomains.map((sub) => (
        <DomainRow key={sub.id} domain={sub} canManage={canManage} ranked={rankedById.get(sub.id)} indent />
      ))}
    </>
  );
}

function gradeRole(grade: string): "good" | "warning" | "critical" {
  if (grade === "A" || grade === "B") return "good";
  if (grade === "C") return "warning";
  return "critical";
}

function DomainRow({
  domain,
  canManage,
  ranked,
  indent,
}: {
  domain: Domain;
  canManage: boolean;
  ranked: RankedDomain | undefined;
  indent: boolean;
}) {
  const [showInstructions, setShowInstructions] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const verifyDomain = useVerifyDomain(domain.id, (result) => {
    if (!result.verified) setShowInstructions(true);
  });

  const deleteDomain = useDeleteDomain(
    domain.id,
    () => {
      setActionError(null);
    },
    (err) => setActionError(err instanceof ApiError ? err.message : "failed to remove domain"),
  );

  const toggleActive = useUpdateDomain(
    domain.id,
    () => {
      setActionError(null);
      setMenuOpen(false);
    },
    (err) => setActionError(err instanceof ApiError ? err.message : "failed to update domain"),
  );

  function handleRemove() {
    setMenuOpen(false);
    if (window.confirm(`Remove ${domain.name}? This can't be undone.`)) {
      deleteDomain.mutate();
    }
  }

  const isPending = domain.verification_status === "pending";
  const hasReports = !!ranked && !ranked.not_verified && !ranked.insufficient_data;
  const counts = ranked?.check_status_counts;
  const reportHours = ranked?.last_report_at ? (Date.now() - new Date(ranked.last_report_at).getTime()) / 3_600_000 : null;
  const colSpan = 8;

  return (
    <>
      <tr style={{ opacity: domain.is_active ? 1 : 0.6 }}>
        <td>
          <div style={{ display: "flex", alignItems: "center", gap: "0.5rem", paddingLeft: indent ? "1.4rem" : 0 }}>
            {indent && <span style={{ color: "var(--ink-muted)" }}>└</span>}
            <Link to={`/domains/${domain.id}`}>
              <strong>{domain.name}</strong>
            </Link>
            {!domain.is_active && <span className="badge badge--neutral">archived</span>}
            <VerificationBadge status={domain.verification_status} />
            {domain.mail_profile !== "sends_mail" && (
              <span className="badge badge--neutral">{MAIL_PROFILE_LABELS[domain.mail_profile]}</span>
            )}
            {canManage && isPending && (
              <>
                <button className="btn btn--ghost btn--sm" onClick={() => setShowInstructions((v) => !v)}>
                  {showInstructions ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  How to verify
                </button>
                <button className="btn btn--secondary btn--sm" onClick={() => verifyDomain.mutate()} disabled={verifyDomain.isPending}>
                  <RefreshCw />
                  Check now
                </button>
              </>
            )}
          </div>
        </td>
        <td>
          {ranked?.grade && (
            <span className={`badge badge--${gradeRole(ranked.grade)}`}>
              {ranked.grade} · {ranked.score}/100
            </span>
          )}
          {!ranked?.grade && <span className="muted">—</span>}
        </td>
        <td>
          {ranked?.current_policy ? <span className="badge badge--neutral">p={ranked.current_policy}</span> : <span className="muted">—</span>}
          {ranked?.ready_to_enforce && (
            <span className="badge badge--good" style={{ marginLeft: "0.3rem" }}>
              ready
            </span>
          )}
        </td>
        {hasReports ? (
          <>
            <td className="num">{ranked!.message_volume.toLocaleString()}</td>
            <td className="num" style={ranked!.failed_volume > 0 ? { color: "var(--critical-text)", fontWeight: 600 } : undefined}>
              {ranked!.failed_volume.toLocaleString()}
            </td>
          </>
        ) : ranked && !ranked.not_verified && ranked.insufficient_data ? (
          <td colSpan={2} className="muted" style={{ fontSize: "0.82rem" }} title={noDataRecommendation(ranked)}>
            No data — {noDataRecommendation(ranked)}
          </td>
        ) : (
          <>
            <td className="muted">—</td>
            <td className="muted">—</td>
          </>
        )}
        <td>
          {reportHours !== null ? (
            <span className="muted">
              <ReportFreshnessValue hours={reportHours} />
            </span>
          ) : (
            <span className="muted">—</span>
          )}
        </td>
        <td>
          {counts ? (
            <span className="muted" style={{ fontSize: "0.8rem" }}>
              {counts.pass} pass
              {counts.warn > 0 && `, ${counts.warn} warn`}
              {counts.fail > 0 && `, ${counts.fail} fail`}
              {counts.error > 0 && `, ${counts.error} error`}
            </span>
          ) : (
            <span className="muted">—</span>
          )}
        </td>
        <td>
          {/* The domain name (first column) already links to the domain's
              overview page — a separate "View" icon here was a redundant
              link to the same place. At the width the browser review used,
              4-5 individual icon buttons wrapped onto multiple lines and
              made rows very tall; consolidating the secondary actions into
              one menu keeps the row a fixed, compact height at any width,
              per the review's own suggested direction, rather than
              reflowing/hiding icons at specific breakpoints. */}
          <div className="chip-row" style={{ justifyContent: "flex-end" }}>
            <div style={{ position: "relative" }}>
              <button className="icon-btn" onClick={() => setMenuOpen((v) => !v)} title="Actions">
                <MoreVertical />
              </button>
              {menuOpen && (
                <>
                  <div style={{ position: "fixed", inset: 0, zIndex: 19 }} onClick={() => setMenuOpen(false)} />
                  <div className="dropdown-menu">
                    {hasReports && (
                      <Link to={`/domains/${domain.id}/reports`} onClick={() => setMenuOpen(false)}>
                        <FileText size={14} />
                        Reports
                      </Link>
                    )}
                    <Link to={`/domains/${domain.id}/senders`} onClick={() => setMenuOpen(false)}>
                      <Users size={14} />
                      Senders
                    </Link>
                    <Link to={`/domains/${domain.id}/dns`} onClick={() => setMenuOpen(false)}>
                      <ShieldCheck size={14} />
                      DNS checks
                    </Link>
                    {canManage && (
                      <>
                        <button onClick={() => toggleActive.mutate({ is_active: !domain.is_active })} disabled={toggleActive.isPending}>
                          {domain.is_active ? <Archive size={14} /> : <ArchiveRestore size={14} />}
                          {domain.is_active ? "Archive" : "Unarchive"}
                        </button>
                        <button onClick={handleRemove} disabled={deleteDomain.isPending}>
                          <Trash2 size={14} />
                          Remove
                        </button>
                      </>
                    )}
                  </div>
                </>
              )}
            </div>
          </div>
        </td>
      </tr>
      {actionError && (
        <tr>
          <td colSpan={colSpan} style={{ paddingTop: 0 }}>
            <div className="alert alert--critical" style={{ marginTop: "0.4rem", marginBottom: 0 }}>
              {actionError}
              {actionError.includes("report history") && " (use Archive instead)"}
            </div>
          </td>
        </tr>
      )}
      {isPending && showInstructions && (
        <tr>
          <td colSpan={colSpan} style={{ paddingTop: 0 }}>
            <div
              style={{
                marginTop: "0.4rem",
                marginBottom: "0.4rem",
                padding: "0.75rem 0.9rem",
                background: "var(--plane)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-sm)",
                fontFamily: "monospace",
                fontSize: "0.83rem",
              }}
            >
              Add this DNS TXT record, then click "Check now":
              <br />
              <strong>{domain.verification_record_name}</strong>
              <br />
              TXT value: <strong>{domain.verification_token}</strong>
              {verifyDomain.isSuccess && !verifyDomain.data.verified && (
                <p style={{ color: "var(--critical-text)", fontFamily: "var(--font-sans)", marginTop: "0.5rem" }}>
                  Not found yet — DNS changes can take a few minutes to propagate.
                </p>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}
