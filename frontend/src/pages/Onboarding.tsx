import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight, RefreshCw, CheckCircle2, KeyRound, Users, ShieldCheck } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { Domain, Organization, VerifyDomainResponse } from "../api/types";
import type { OnboardingStatus } from "../api/onboarding";
import type { CheckResult } from "../api/dnsChecks";
import type { MailboxConnectionStatus } from "../api/dmarc";
import MailboxConnectionSection from "../components/settings/MailboxConnectionSection";
import AddDomainForm from "../components/settings/AddDomainForm";
import PolicyBuilder from "../components/policy-builder/PolicyBuilder";
import { MailboxHealthWidget } from "../components/overview/widgets";

const STEP_LABELS = ["Welcome", "Mailbox", "Domain", "Verify", "DNS baseline", "Reporting", "Waiting room"];

function deriveStep(status: OnboardingStatus, hasEntraTenant: boolean): number {
  // No "have they seen Welcome" flag is persisted anywhere (deliberately
  // stateless, see onboarding_status's docstring), so this can't
  // distinguish "never visited" from "read Welcome, still working on the
  // next milestone." Resolved in Welcome's favor: reshow it on reload
  // rather than silently skip it for an account that's done nothing yet.
  //
  // "Done nothing yet" is judged by each org type's own first real
  // action — has_mailbox for Entra orgs, has_domain for local-auth orgs
  // (which have no mailbox step at all, see MailboxStep).
  const firstMilestoneDone = hasEntraTenant ? status.has_mailbox : status.has_domain;
  if (!firstMilestoneDone) return 1;
  if (!status.has_domain) return 3;
  if (!status.has_verified_domain) return 4;
  if (!status.has_dns_baseline) return 5;
  return 6;
}

export default function Onboarding() {
  const { data: status } = useQuery({
    queryKey: ["onboarding-status"],
    queryFn: () => api.get<OnboardingStatus>("/onboarding/status"),
  });
  const { data: org } = useQuery({
    queryKey: ["organization", "current"],
    queryFn: () => api.get<Organization>("/organizations/current"),
  });
  const { data: domains } = useQuery({
    queryKey: ["domains"],
    queryFn: () => api.get<Domain[]>("/domains"),
    // Live-updates VerifyStep/BaselineStep while DNS propagates, so a
    // background verification (see run_domain_verification_sweep) shows up
    // here without the user needing to do anything.
    refetchInterval: (query) => {
      const apex = (query.state.data ?? []).filter((d) => !d.parent_domain_id);
      return apex.some((d) => d.verification_status === "pending") ? 10000 : false;
    },
  });

  const [step, setStep] = useState<number | null>(null);

  // Seed the wizard to wherever setup actually is, once, the first time
  // status AND org have loaded — after that the user drives Back/Next
  // themselves so finishing one step doesn't yank them straight to the
  // next before they've had a chance to read the confirmation.
  useEffect(() => {
    if (status && org && step === null) setStep(deriveStep(status, !!org.entra_tenant_id));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status !== undefined, org !== undefined]);

  const apexDomains = (domains ?? []).filter((d) => !d.parent_domain_id);
  const focusDomain = [...apexDomains].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];

  if (!status || step === null) {
    return <p className="muted" style={{ padding: "2rem" }}>Loading…</p>;
  }

  return (
    <section>
      <div className="page-header">
        <div>
          <h1>Set up YetAnotherDmarcTool</h1>
          <p className="page-subtitle">Connect your mailbox, add a domain, and get a DNS baseline — a few minutes, most of it waiting on DNS.</p>
        </div>
      </div>

      <div className="chip-row" style={{ marginBottom: "1.5rem" }}>
        {STEP_LABELS.map((label, i) => {
          const n = i + 1;
          return (
            <span
              key={label}
              className={`badge ${n === step ? "badge--good" : n < step ? "badge--neutral" : "badge--neutral"}`}
              style={n > step ? { opacity: 0.5 } : undefined}
            >
              {n}. {label}
            </span>
          );
        })}
      </div>

      <div className="card">
        {step === 1 && <WelcomeStep status={status} onNext={() => setStep(2)} />}
        {step === 2 && <MailboxStep onBack={() => setStep(1)} onNext={() => setStep(3)} />}
        {step === 3 && <DomainStep onBack={() => setStep(2)} onNext={() => setStep(4)} />}
        {step === 4 && <VerifyStep domain={focusDomain} onBack={() => setStep(3)} onNext={() => setStep(5)} />}
        {step === 5 && <BaselineStep domain={focusDomain} onBack={() => setStep(4)} onNext={() => setStep(6)} />}
        {step === 6 && <ReportingStep domain={focusDomain} onBack={() => setStep(5)} onNext={() => setStep(7)} />}
        {step === 7 && <WaitingRoomStep domain={focusDomain} onBack={() => setStep(6)} />}
      </div>
    </section>
  );
}

function StepNav({ onBack, onNext, nextLabel = "Continue" }: { onBack?: () => void; onNext?: () => void; nextLabel?: string }) {
  return (
    <div className="chip-row" style={{ marginTop: "1.25rem" }}>
      {onBack && (
        <button className="btn btn--ghost btn--sm" onClick={onBack}>
          <ArrowLeft />
          Back
        </button>
      )}
      {onNext && (
        <button className="btn btn--primary btn--sm" onClick={onNext}>
          {nextLabel}
          <ArrowRight />
        </button>
      )}
    </div>
  );
}

function WelcomeStep({ status, onNext }: { status: OnboardingStatus; onNext: () => void }) {
  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Welcome to YetAnotherDmarcTool</h2>
      <p>
        Your organization, <strong>{status.org_name}</strong>, has already been set up by your platform
        administrator. You're signed in as {status.user_role === "org_admin" ? "an organization admin" : "a member"}.
      </p>
      <p className="muted">
        Next: connect the mailbox your DMARC/TLS-RPT reports arrive at, add your first domain, verify you own it,
        and run a DNS baseline — then we'll show you what's next while the first reports arrive.
      </p>
      <StepNav onNext={onNext} nextLabel="Get started" />
    </div>
  );
}

function MailboxStep({ onBack, onNext }: { onBack: () => void; onNext: () => void }) {
  // Same query key/options MailboxConnectionSection uses below, so
  // react-query dedupes into one shared fetch/poll rather than two.
  const { data: connection } = useQuery({
    queryKey: ["mailbox-connection"],
    queryFn: () => api.get<MailboxConnectionStatus>("/mailbox-connection"),
    retry: false,
  });
  const { data: org } = useQuery({
    queryKey: ["organization", "current"],
    queryFn: () => api.get<Organization>("/organizations/current"),
  });

  // No Entra tenant means no Mail Access consent is possible — this org
  // gets a hosted address per domain instead (offered later, once a domain
  // exists — see VerifyStep/ReportingStep's PolicyBuilder), so there's
  // nothing to connect or gate on here at all.
  if (org && !org.entra_tenant_id) {
    return (
      <div>
        <h2 style={{ marginTop: 0 }}>Report mailbox</h2>
        <div className="alert alert--good">
          Your organization signs in without Microsoft Entra, so there's no mailbox to connect here. Once you add a
          domain, we'll generate a dedicated YetAnotherDmarcTool-hosted reporting address for it automatically.
        </div>
        <StepNav onBack={onBack} onNext={onNext} />
      </div>
    );
  }

  // Gate on "connected and not erroring," not "fully synced" — a fresh
  // save has last_sync_status === null until the background sync resolves,
  // and blocking Next on that would turn the soft gate into a hard one.
  const ready = connection !== undefined && connection.consent_status === "granted" && connection.last_sync_status !== "error";

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Connect your report mailbox</h2>
      <p className="section-hint">
        We only read DMARC/TLS-RPT report messages and their attachments from this mailbox.
      </p>
      <MailboxConnectionSection canManage={true} />
      {connection?.last_sync_status === "error" && (
        <p className="section-hint" style={{ color: "var(--critical-text)" }}>
          Fix the connection above — grant mail access, then save the mailbox address again — before continuing.
        </p>
      )}
      <StepNav onBack={onBack} onNext={ready ? onNext : undefined} />
    </div>
  );
}

function DomainStep({ onBack, onNext }: { onBack: () => void; onNext: () => void }) {
  const { data: domains } = useQuery({
    queryKey: ["domains"],
    queryFn: () => api.get<Domain[]>("/domains"),
  });
  const { data: org } = useQuery({
    queryKey: ["organization", "current"],
    queryFn: () => api.get<Organization>("/organizations/current"),
  });
  const hasDomain = (domains ?? []).length > 0;
  const apexDomains = (domains ?? []).filter((d) => !d.parent_domain_id);
  const latestDomain = [...apexDomains].sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const hostedOnly = !!org && !org.entra_tenant_id;

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Add your first domain</h2>
      <p className="section-hint">Start with the domain you send the most mail from — you can add more later.</p>
      <AddDomainForm />
      {hostedOnly && latestDomain && <HostedAddressPreview domain={latestDomain} />}
      <StepNav onBack={onBack} onNext={hasDomain ? onNext : undefined} />
    </div>
  );
}

// Auto-generates (idempotent — same POST DomainStep's PolicyBuilder steps
// use later) and reveals the domain's hosted reporting address right when
// it's most concrete: the moment the domain exists. Saves a trip to the
// Policy Builder later just to find out what address to expect.
function HostedAddressPreview({ domain }: { domain: Domain }) {
  const [address, setAddress] = useState<string | null>(domain.hosted_report_address);
  const [error, setError] = useState<string | null>(null);
  const requestedFor = useRef<string | null>(null);

  const generate = useMutation({
    mutationFn: () => api.post<{ hosted_report_address: string }>(`/domains/${domain.id}/hosted-report-address`),
    onSuccess: (result) => setAddress(result.hosted_report_address),
    onError: (err) => setError(err instanceof ApiError ? err.message : "couldn't generate a hosted address"),
  });

  useEffect(() => {
    if (!domain.hosted_report_address && requestedFor.current !== domain.id) {
      requestedFor.current = domain.id;
      generate.mutate();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domain.id, domain.hosted_report_address]);

  if (error) {
    return (
      <p className="section-hint" style={{ color: "var(--critical-text)", marginTop: "0.6rem", marginBottom: 0 }}>
        Couldn't generate a hosted address yet: {error}
      </p>
    );
  }
  if (!address) return null;

  return (
    <div className="alert alert--good" style={{ marginTop: "0.75rem" }}>
      Reports for {domain.name} will arrive at:
      <div className="chip-row" style={{ marginTop: "0.4rem" }}>
        <code>{address}</code>
        <span className="badge badge--good">hosted by YetAnotherDmarcTool</span>
      </div>
    </div>
  );
}

function VerifyStep({ domain, onBack, onNext }: { domain: Domain | undefined; onBack: () => void; onNext: () => void }) {
  const queryClient = useQueryClient();
  const [showBuilder, setShowBuilder] = useState(false);
  const verifyDomain = useMutation({
    mutationFn: () => api.post<VerifyDomainResponse>(`/domains/${domain!.id}/verify`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["domains"] });
      queryClient.invalidateQueries({ queryKey: ["onboarding-status"] });
    },
  });

  if (!domain) {
    return <p className="muted">No domain to verify yet — go back and add one first.</p>;
  }

  const verified = domain.verification_status === "verified";

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Verify you own {domain.name}</h2>
      <p className="section-hint">Add this DNS TXT record to prove ownership before checks can run.</p>

      {verified ? (
        <div className="alert alert--good">
          <CheckCircle2 size={15} style={{ verticalAlign: "-2px", marginRight: "0.4rem" }} />
          {domain.name} is verified.
        </div>
      ) : (
        <div
          style={{
            padding: "0.75rem 0.9rem",
            background: "var(--plane)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-sm)",
            fontFamily: "monospace",
            fontSize: "0.83rem",
          }}
        >
          Host/name: <strong>{domain.verification_record_name}</strong>
          <br />
          Type: <strong>TXT</strong>
          <br />
          Value: <strong>{domain.verification_token}</strong>
          {verifyDomain.isSuccess && !verifyDomain.data.verified && (
            <p style={{ color: "var(--critical-text)", fontFamily: "var(--font-sans)", marginTop: "0.5rem", marginBottom: 0 }}>
              Not found yet — DNS changes can take a few minutes to propagate.
            </p>
          )}
          <p className="muted" style={{ fontFamily: "var(--font-sans)", marginTop: "0.5rem", marginBottom: 0 }}>
            DNS changes can take a while to propagate — we check automatically every few minutes, so you don't have
            to wait here. Use "Check now" if you want to confirm sooner.
          </p>
        </div>
      )}

      <div className="chip-row" style={{ marginTop: "0.75rem" }}>
        {!verified && (
          <button className="btn btn--secondary btn--sm" onClick={() => verifyDomain.mutate()} disabled={verifyDomain.isPending}>
            <RefreshCw />
            {verifyDomain.isPending ? "Checking…" : "Check now"}
          </button>
        )}
      </div>

      <div style={{ marginTop: "1rem", paddingTop: "0.75rem", borderTop: "1px solid var(--border)" }}>
        <div className="stat-tile-label" style={{ marginBottom: "0.3rem" }}>
          Also publish your DMARC record
        </div>
        <p className="section-hint" style={{ marginTop: 0, marginBottom: "0.5rem" }}>
          While you're already updating DNS for {domain.name}, you can publish a starting DMARC record too — saves
          a second trip to your DNS provider later.
        </p>
        <button className="btn btn--secondary btn--sm" onClick={() => setShowBuilder(true)}>
          Build DMARC policy
        </button>
      </div>
      {showBuilder && <PolicyBuilder domainId={domain.id} domainName={domain.name} onClose={() => setShowBuilder(false)} />}

      <StepNav onBack={onBack} onNext={onNext} nextLabel={verified ? "Continue" : "Continue — I'll finish verifying later"} />
    </div>
  );
}

function BaselineStep({ domain, onBack, onNext }: { domain: Domain | undefined; onBack: () => void; onNext: () => void }) {
  const [ranOnce, setRanOnce] = useState(false);
  const recheck = useMutation({
    mutationFn: () => api.post<CheckResult[]>(`/domains/${domain!.id}/checks/recheck`),
    onSuccess: () => setRanOnce(true),
  });

  if (!domain) {
    return <p className="muted">No verified domain yet — go back and verify one first.</p>;
  }

  if (domain.verification_status === "pending") {
    return (
      <div>
        <h2 style={{ marginTop: 0 }}>Run a DNS baseline for {domain.name}</h2>
        <div className="alert alert--warning">
          Still waiting on DNS verification for {domain.name} — the baseline check needs that to finish first.
          We're checking automatically every few minutes; go{" "}
          <button
            className="btn btn--ghost btn--sm"
            onClick={onBack}
            style={{ display: "inline-flex", verticalAlign: "-2px" }}
          >
            back to Verify
          </button>{" "}
          to check now, or continue and come back once it's verified.
        </div>
        <StepNav onBack={onBack} onNext={onNext} nextLabel="Skip for now" />
      </div>
    );
  }

  const results = recheck.data ?? [];
  const counts = results.reduce(
    (acc, r) => ({ ...acc, [r.status]: (acc[r.status as keyof typeof acc] ?? 0) + 1 }),
    { pass: 0, warn: 0, fail: 0, error: 0 },
  );

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Run a DNS baseline for {domain.name}</h2>
      <p className="section-hint">
        Checks SPF, DKIM, DMARC, MX, STARTTLS, MTA-STS, TLS-RPT, and DANE. Individual warnings don't block setup —
        you'll see the full breakdown on the domain page.
      </p>

      {!ranOnce && (
        <button className="btn btn--primary btn--sm" onClick={() => recheck.mutate()} disabled={recheck.isPending}>
          <RefreshCw />
          {recheck.isPending ? "Checking…" : "Run baseline check"}
        </button>
      )}

      {ranOnce && (
        <div className="alert alert--good">
          Baseline created — {counts.pass} passing, {counts.warn} warnings, {counts.fail} failing, {counts.error} couldn't be checked.{" "}
          <Link to={`/domains/${domain.id}`}>See the full breakdown</Link>.
        </div>
      )}
      {recheck.isError && (
        <div className="alert alert--warning">
          {recheck.error instanceof ApiError ? recheck.error.message : "Couldn't run the baseline check — try again."}
        </div>
      )}

      <StepNav onBack={onBack} onNext={ranOnce ? onNext : undefined} />
    </div>
  );
}

function ReportingStep({ domain, onBack, onNext }: { domain: Domain | undefined; onBack: () => void; onNext: () => void }) {
  const [showBuilder, setShowBuilder] = useState(false);
  const { data, isLoading } = useQuery({
    queryKey: ["rua-check", domain?.id],
    queryFn: () => api.get<{ status: string; org_mailbox_address: string | null }>(`/domains/${domain!.id}/dmarc/rua-check`),
    enabled: !!domain,
  });

  if (!domain) {
    return <p className="muted">No verified domain yet — go back and verify one first.</p>;
  }

  const correct = data?.status === "correct";

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Make sure reports reach this dashboard</h2>
      <p className="section-hint">
        Your DMARC record's <code>rua=</code> address has to point at your connected mailbox, or reports will never
        arrive here.
      </p>

      {isLoading && <p className="muted">Checking…</p>}
      {data && (
        <div className={`alert ${correct ? "alert--good" : "alert--warning"}`}>
          {correct
            ? "Aggregate reports are already configured to reach your connected mailbox."
            : "Your DMARC record's rua= doesn't point at your connected mailbox yet."}
          {!correct && (
            <div style={{ marginTop: "0.6rem" }}>
              <button className="btn btn--secondary btn--sm" onClick={() => setShowBuilder(true)}>
                Build DMARC policy
              </button>
            </div>
          )}
        </div>
      )}

      {showBuilder && <PolicyBuilder domainId={domain.id} domainName={domain.name} onClose={() => setShowBuilder(false)} />}

      <StepNav onBack={onBack} onNext={onNext} />
    </div>
  );
}

function WaitingRoomStep({ domain, onBack }: { domain: Domain | undefined; onBack: () => void }) {
  const { data: connection, isLoading: mailboxLoading } = useQuery({
    queryKey: ["mailbox-connection"],
    queryFn: () => api.get<MailboxConnectionStatus>("/mailbox-connection"),
    retry: false,
  });

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Waiting for your first report</h2>
      <p className="section-hint">
        DMARC aggregate reports usually arrive daily — most senders publish their first one within 24–48 hours of
        seeing your updated <code>rua=</code> address. You'll land on the full dashboard automatically once reports
        start arriving.
      </p>

      <div className="chip-row" style={{ marginBottom: "1rem" }}>
        <MailboxHealthWidget connection={connection} isLoading={mailboxLoading} />
      </div>

      <div className="stat-tile-label" style={{ marginBottom: "0.5rem" }}>
        While you wait
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
        <Link to="/settings" className="chip-row" style={{ textDecoration: "none" }}>
          <ArrowRight size={14} /> Add another domain
        </Link>
        {domain && (
          <Link to={`/domains/${domain.id}`} className="chip-row" style={{ textDecoration: "none" }}>
            <KeyRound size={14} /> Add DKIM selectors
          </Link>
        )}
        <Link to="/team" className="chip-row" style={{ textDecoration: "none" }}>
          <Users size={14} /> Share your sign-in link with your team
        </Link>
        {domain && (
          <Link to={`/domains/${domain.id}`} className="chip-row" style={{ textDecoration: "none" }}>
            <ShieldCheck size={14} /> Review DNS recommendations
          </Link>
        )}
      </div>

      <StepNav onBack={onBack} />
      <p style={{ marginTop: "1rem" }}>
        <Link to="/">Go to the dashboard now</Link>
      </p>
    </div>
  );
}
