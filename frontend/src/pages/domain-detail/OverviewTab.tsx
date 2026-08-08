import { useOutletContext, Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertOctagon, AlertTriangle, CheckCircle2, Info, ArrowRight } from "lucide-react";
import { api } from "../../api/client";
import type { Domain, DomainMailProfile } from "../../api/types";
import type { DmarcSummary, DomainRating } from "../../api/dmarc";
import type { ActionItem } from "../../api/overview";
import { MailProfileSelect, Stat } from "../../components/domain/shared";
import DomainRatingCard from "../../components/domain/DomainRatingCard";
import { IssueRow } from "../../components/shared/IssueRow";
import { useAuth } from "../../auth/AuthContext";

const SEVERITY_ICON: Record<ActionItem["severity"], typeof AlertTriangle> = {
  critical: AlertOctagon,
  serious: AlertOctagon,
  warning: AlertTriangle,
  good: CheckCircle2,
  neutral: Info,
};

export default function OverviewTab() {
  const domain = useOutletContext<Domain>();
  const { user } = useAuth();
  const canManage = user?.role === "org_admin";
  const queryClient = useQueryClient();

  const setMailProfile = useMutation({
    mutationFn: (mail_profile: DomainMailProfile) => api.patch<Domain>(`/domains/${domain.id}`, { mail_profile }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["domains", domain.id] });
      queryClient.invalidateQueries({ queryKey: ["domains"] });
      queryClient.invalidateQueries({ queryKey: ["domains-ranked"] });
      queryClient.invalidateQueries({ queryKey: ["action-queue", domain.id] });
    },
  });

  const { data: summary, isLoading: summaryLoading } = useQuery({
    queryKey: ["dmarc-summary", domain.id],
    queryFn: () => api.get<DmarcSummary>(`/domains/${domain.id}/dmarc/summary`),
  });

  const { data: rating } = useQuery({
    queryKey: ["domain-rating", domain.id],
    queryFn: () => api.get<DomainRating>(`/domains/${domain.id}/rating`),
  });

  const { data: fixes } = useQuery({
    queryKey: ["action-queue", domain.id],
    queryFn: () => api.get<ActionItem[]>(`/action-queue?domain_id=${domain.id}`),
  });

  const passRate =
    summary && summary.total_message_count > 0
      ? Math.round((summary.dmarc_pass_count / summary.total_message_count) * 100)
      : null;

  const topFix = fixes?.[0];

  return (
    <>
      <div className="card">
        <div className="chip-row" style={{ justifyContent: "space-between" }}>
          <div className="stat-tile-label" style={{ margin: 0 }}>
            Mail profile
          </div>
          {canManage ? (
            <MailProfileSelect
              value={domain.mail_profile}
              onChange={(value) => setMailProfile.mutate(value)}
              className="input"
            />
          ) : (
            <span className="badge badge--neutral">{domain.mail_profile === "sends_mail" ? "Sends mail" : domain.mail_profile}</span>
          )}
        </div>
        {domain.mail_profile !== "sends_mail" && (
          <p className="section-hint" style={{ marginTop: "0.5rem", marginBottom: 0 }}>
            No legitimate outbound mail expected — the policy builder recommends locking straight to p=reject
            instead of waiting on report data.
          </p>
        )}
      </div>

      {summary && summary.report_count > 0 && (
        <div className="card">
          <div className="stat-row" style={{ marginBottom: topFix ? "0.9rem" : 0 }}>
            {rating && !rating.not_verified && !rating.insufficient_data && rating.score !== null && (
              <Stat label={`Grade ${rating.grade}`} value={`${rating.score}%`} />
            )}
            <Stat label="DMARC pass rate" value={passRate !== null ? `${passRate}%` : "—"} />
            <Stat label="Failing messages" value={summary.dmarc_fail_count} />
            <Stat label="Current policy" value={summary.current_policy ? `p=${summary.current_policy}` : "—"} />
          </div>
          {topFix && (
            <div
              className={`alert alert--${topFix.severity}`}
              style={{ marginBottom: 0, display: "flex", gap: "0.6rem", alignItems: "flex-start" }}
            >
              {(() => {
                const Icon = SEVERITY_ICON[topFix.severity];
                return <Icon style={{ flexShrink: 0, marginTop: "0.15rem", width: 16, height: 16 }} />;
              })()}
              <div>
                <div style={{ fontWeight: 600 }}>Top recommended fix: {topFix.title}</div>
                <div style={{ marginTop: "0.15rem" }}>{topFix.action_hint}</div>
              </div>
            </div>
          )}
        </div>
      )}

      <DomainRatingCard rating={rating} />

      {summaryLoading && <p className="muted">Loading…</p>}
      {summary && summary.report_count === 0 && (
        <p className="empty-state">No aggregate reports received yet for this domain.</p>
      )}

      {fixes && fixes.length > 0 && (
        <div className="card">
          <div className="card-header">
            <h3>Fixes</h3>
          </div>
          {fixes.slice(0, 3).map((item, i) => (
            <IssueRow key={i} item={item} />
          ))}
          <Link to="fixes" className="btn btn--ghost btn--sm" style={{ marginTop: "0.5rem" }}>
            View all fixes ({fixes.length})
            <ArrowRight size={14} />
          </Link>
        </div>
      )}
    </>
  );
}
