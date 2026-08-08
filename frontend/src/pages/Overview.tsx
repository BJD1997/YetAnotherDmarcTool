import { Link, Navigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, KeyRound, ShieldCheck, Users } from "lucide-react";
import { api } from "../api/client";
import type { Domain } from "../api/types";
import type { Posture, TrendPoint } from "../api/overview";
import type { OnboardingStatus } from "../api/onboarding";
import type { MailboxConnectionStatus } from "../api/dmarc";
import { DATE_RANGE_PRESETS } from "../api/overview";
import CommandBar from "../components/overview/CommandBar";
import PostureStrip from "../components/overview/PostureStrip";
import TrendChart from "../components/overview/TrendChart";
import ActionQueue from "../components/overview/ActionQueue";
import DomainsNeedingAttention from "../components/overview/DomainsNeedingAttention";
import SenderInventory from "../components/overview/SenderInventory";
import { MailboxHealthWidget } from "../components/overview/widgets";

export default function Overview() {
  const [params, setParams] = useSearchParams();
  const domainId = params.get("domain");
  const requestedDays = Number(params.get("days") ?? "30");
  const days = (DATE_RANGE_PRESETS as readonly number[]).includes(requestedDays) ? requestedDays : 30;

  const { data: domains } = useQuery({
    queryKey: ["domains"],
    queryFn: () => api.get<Domain[]>("/domains"),
  });

  const { data: onboarding, isLoading: onboardingLoading } = useQuery({
    queryKey: ["onboarding-status"],
    queryFn: () => api.get<OnboardingStatus>("/onboarding/status"),
  });

  function setDomain(id: string | null) {
    const next = new URLSearchParams(params);
    if (id) next.set("domain", id);
    else next.delete("domain");
    setParams(next, { replace: true });
  }

  function setDays(next_days: number) {
    const next = new URLSearchParams(params);
    next.set("days", String(next_days));
    setParams(next, { replace: true });
  }

  async function handleExport() {
    const suffix = `days=${days}${domainId ? `&domain_id=${domainId}` : ""}`;
    const [trend, posture] = await Promise.all([
      api.get<TrendPoint[]>(`/dmarc/trend?${suffix}`),
      api.get<Posture>(`/dmarc/posture?${suffix}`),
    ]);

    const lines = [
      `# YetAnotherDmarcTool overview export — ${domainId ?? "all domains"}, last ${days} days`,
      `# compliance_pct,${posture.compliance_pct ?? ""}`,
      `# failed_volume,${posture.failed_volume}`,
      `# new_sender_count,${posture.new_sender_count}`,
      `# ready_to_enforce_count,${posture.ready_to_enforce_count}`,
      "",
      "date,total,dmarc_pass,spf_aligned,dkim_aligned,rejected",
      ...trend.map((t) => [t.date, t.total, t.dmarc_pass, t.spf_aligned, t.dkim_aligned, t.rejected].join(",")),
    ];

    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `dmarc-overview-${domainId ?? "all"}-${days}d.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  if (onboardingLoading || !onboarding) {
    return <p className="muted" style={{ padding: "2rem" }}>Loading…</p>;
  }

  if (!onboarding.has_mailbox || !onboarding.has_verified_domain) {
    return <Navigate to="/onboarding" replace />;
  }

  if (!onboarding.has_any_report) {
    return <WaitingForReports domains={domains ?? []} />;
  }

  return (
    <section className="overview-page">
      <div className="page-header">
        <div>
          <h1>Overview</h1>
          <p className="page-subtitle">
            Authentication posture, senders, and open issues across your domains, in one place.
          </p>
        </div>
      </div>

      <CommandBar
        domains={domains ?? []}
        domainId={domainId}
        onDomainChange={setDomain}
        days={days}
        onDaysChange={setDays}
        onExport={handleExport}
      />

      <PostureStrip domainId={domainId} days={days} />

      <div className="overview-main-grid">
        <TrendChart domainId={domainId} days={days} />
        <div className="overview-side-stack">
          <div className="card">
            <ActionQueue domainId={domainId} />
            {!domainId && <DomainsNeedingAttention />}
          </div>
        </div>
      </div>

      <SenderInventory domainId={domainId} domains={domains ?? []} />
    </section>
  );
}

function WaitingForReports({ domains }: { domains: Domain[] }) {
  const { data: connection, isLoading: mailboxLoading } = useQuery({
    queryKey: ["mailbox-connection"],
    queryFn: () => api.get<MailboxConnectionStatus>("/mailbox-connection"),
    retry: false,
  });
  const focusDomain = [...domains.filter((d) => !d.parent_domain_id)].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];

  return (
    <section>
      <div className="page-header">
        <div>
          <h1>Overview</h1>
          <p className="page-subtitle">Setup is done — waiting for your first DMARC aggregate report to arrive.</p>
        </div>
      </div>

      <div className="card">
        <div className="chip-row" style={{ marginBottom: "0.75rem" }}>
          <MailboxHealthWidget connection={connection} isLoading={mailboxLoading} />
        </div>
        <p className="muted" style={{ marginBottom: 0 }}>
          DMARC aggregate reports usually arrive daily — most senders publish their first one within 24–48 hours.
          This page will switch to your real posture, senders, and action queue automatically once reports start
          arriving.
        </p>
      </div>

      <div className="card">
        <div className="stat-tile-label" style={{ marginBottom: "0.6rem" }}>
          While you wait
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
          <Link to="/settings" className="chip-row" style={{ textDecoration: "none" }}>
            <ArrowRight size={14} /> Add another domain
          </Link>
          {focusDomain && (
            <Link to={`/domains/${focusDomain.id}`} className="chip-row" style={{ textDecoration: "none" }}>
              <KeyRound size={14} /> Add DKIM selectors
            </Link>
          )}
          <Link to="/team" className="chip-row" style={{ textDecoration: "none" }}>
            <Users size={14} /> Share your sign-in link with your team
          </Link>
          {focusDomain && (
            <Link to={`/domains/${focusDomain.id}`} className="chip-row" style={{ textDecoration: "none" }}>
              <ShieldCheck size={14} /> Review DNS recommendations
            </Link>
          )}
        </div>
      </div>
    </section>
  );
}
