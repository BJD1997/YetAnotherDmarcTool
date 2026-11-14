import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowLeft, ArrowRight, Check, RefreshCw, CheckCircle2, KeyRound, Users, ShieldCheck } from "lucide-react";
import { ApiError } from "../api/client";
import type { Domain } from "../api/types";
import type { OnboardingStatus } from "../api/onboarding";
import MailboxConnectionSection from "../components/settings/MailboxConnectionSection";
import AddDomainForm from "../components/settings/AddDomainForm";
import CopyValue from "../components/CopyValue";
import SignInTab from "./settings/SignInTab";
import PolicyBuilder from "../components/policy-builder/PolicyBuilder";
import { MailboxHealthWidget } from "../components/overview/widgets";
import { useDomains, useVerifyDomain } from "../hooks/useDomains";
import { useMailboxConnection } from "../hooks/useMailboxConnection";
import { useSignInSettings } from "../hooks/useSignInSettings";
import { queryKeys } from "../hooks/queryKeys";
import { useCompleteOnboarding, useGenerateHostedReportAddress, useOnboardingStatus } from "../hooks/useOnboarding";
import { useCurrentOrganization, useUpdateOrganization } from "../hooks/useOrganization";
import { useRecheckDns } from "../hooks/useDnsChecks";
import { useRuaCheck } from "../hooks/useDomainInsights";
import { ASK_AI_EXPLANATION, AskAiToggle } from "../components/settings/AskAiToggle";
import { useAuth } from "../auth/AuthContext";
import type { Organization } from "../api/types";
import { CHECK_LABELS, type CheckResult } from "../api/dnsChecks";

// Read by screen readers, not shown: the check icon says "done" visually.
const VISUALLY_HIDDEN = {
  position: "absolute",
  width: 1,
  height: 1,
  overflow: "hidden",
  clip: "rect(0 0 0 0)",
  whiteSpace: "nowrap",
} as const;

const STEP_LABELS = ["Welcome", "Domain", "Sign-in", "Report mailbox", "DNS baseline", "Reporting", "AI help", "Waiting room"];

// Resumes at the first unfinished step, judged only by what's actually set
// up, so whoever opens the wizard (break-glass or a later admin, after
// doing things in Settings) lands in the right place. Sign-in counts as
// done once a report destination exists: "Only local accounts for now"
// isn't stored, and getting past it is the only way to the mailbox step.
export function deriveStep(status: OnboardingStatus): number {
  const started = status.has_domain || status.has_mailbox || status.has_sign_in_provider;
  if (!started) return 1;
  if (!status.has_verified_domain) return 2;
  if (!status.has_sign_in_provider && !status.has_mailbox) return 3;
  if (!status.has_mailbox) return 4;
  if (!status.has_dns_baseline) return 5;
  return 6;
}

export default function Onboarding() {
  const { data: status } = useOnboardingStatus();
  const { data: org } = useCurrentOrganization();
  const { data: domains } = useDomains({
    // Live-updates DomainStep/BaselineStep while DNS propagates, so a
    // background verification (see run_domain_verification_sweep) shows up
    // here without the user needing to do anything.
    refetchInterval: (query) => {
      const apex = (query.state.data ?? []).filter((d) => !d.parent_domain_id);
      return apex.some((d) => d.verification_status === "pending") ? 10000 : false;
    },
  });

  const [step, setStep] = useState<number | null>(null);

  // Seed the wizard to wherever setup actually is, once, the first time
  // status has loaded — after that the user drives Back/Next themselves so
  // finishing one step doesn't yank them straight to the next before
  // they've had a chance to read the confirmation.
  useEffect(() => {
    if (status && step === null) setStep(deriveStep(status));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status !== undefined]);

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
          <p className="page-subtitle">
            Add and verify a domain, choose how people sign in and where reports go, and get a DNS baseline — a few
            minutes, most of it waiting on DNS.
          </p>
        </div>
      </div>

      <div className="chip-row" style={{ marginBottom: "1.5rem" }}>
        {STEP_LABELS.map((label, i) => {
          const n = i + 1;
          const done = n < step;
          return (
            <span
              key={label}
              data-testid={`step-chip-${n}`}
              className={`badge ${n === step ? "badge--good" : "badge--neutral"}`}
              style={n > step ? { opacity: 0.5 } : undefined}
            >
              {done && <Check size={12} aria-hidden="true" />}
              {n}. {label}
              {done && <span style={VISUALLY_HIDDEN}> (done)</span>}
            </span>
          );
        })}
      </div>

      <div className="card">
        {step === 1 && <WelcomeStep status={status} onNext={() => setStep(2)} />}
        {step === 2 && <DomainStep domain={focusDomain} onBack={() => setStep(1)} onNext={() => setStep(3)} />}
        {step === 3 && <SignInStep onBack={() => setStep(2)} onNext={() => setStep(4)} />}
        {step === 4 && <MailboxStep domain={focusDomain} onBack={() => setStep(3)} onNext={() => setStep(5)} />}
        {step === 5 && (
          <BaselineStep domain={focusDomain} onBack={() => setStep(4)} onVerify={() => setStep(2)} onNext={() => setStep(6)} />
        )}
        {step === 6 && <ReportingStep domain={focusDomain} onBack={() => setStep(5)} onNext={() => setStep(7)} />}
        {step === 7 && org && <AskAiStep org={org} onBack={() => setStep(6)} onNext={() => setStep(8)} />}
        {step === 8 && <WaitingRoomStep domain={focusDomain} onBack={() => setStep(7)} />}
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

function AskAiStep({ org, onBack, onNext }: { org: Organization; onBack: () => void; onNext: () => void }) {
  const { user } = useAuth();
  const decline = useUpdateOrganization();
  // Continuing without ticking the box is an answer too ("no"), so the
  // one-time question doesn't ask again later.
  function next() {
    if (user?.role === "org_admin" && org.ask_ai_enabled === null) decline.mutate({ name: org.name, ask_ai_enabled: false });
    onNext();
  }
  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Help from an AI assistant (optional)</h2>
      <p className="section-hint">{ASK_AI_EXPLANATION}</p>
      {user?.role === "org_admin" ? (
        <AskAiToggle org={org} />
      ) : (
        <p className="muted">An org admin can turn this on under Settings → General.</p>
      )}
      <p className="muted" style={{ fontSize: "0.82rem" }}>Off unless you turn it on; you can change it any time in Settings → General.</p>
      <StepNav onBack={onBack} onNext={next} />
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
        Next: add your first domain and verify you own it, choose how people sign in, pick where your DMARC/TLS-RPT
        reports arrive, and run a DNS baseline — then we'll show you what's next while the first reports arrive.
      </p>
      <StepNav onNext={onNext} nextLabel="Get started" />
    </div>
  );
}

/** True when this server has no hosted mailbox and the organization has no
 *  report mailbox of its own either, so reports have nowhere to arrive. */
function useNoReportMailbox(): boolean {
  const { data: status } = useOnboardingStatus();
  return !!status && !status.hosted_mailbox_ready && !status.has_mailbox;
}

function NoReportMailboxNotice() {
  return (
    <div className="alert alert--warning">
      <strong>No report mailbox yet.</strong> This server has no hosted reporting mailbox, so connect the mailbox your
      DMARC reports arrive at (the Report mailbox step of this wizard, or Settings → General). You can add and verify
      domains and run DNS checks meanwhile. Administrators can also set up a hosted mailbox (see the{" "}
      <a href="https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Configuration-Reference" target="_blank" rel="noreferrer">
        Configuration reference
      </a>
      ).
    </div>
  );
}

// Adds the first domain, then shows its ownership record until DNS has it.
// Verification comes first: sign-in providers and hosted addresses both
// need a verified domain.
function DomainStep({ domain, onBack, onNext }: { domain: Domain | undefined; onBack: () => void; onNext: () => void }) {
  const [showBuilder, setShowBuilder] = useState(false);
  const verifyDomain = useVerifyDomain(domain?.id ?? "");

  if (!domain) {
    return (
      <div>
        <h2 style={{ marginTop: 0 }}>Add your first domain</h2>
        <p className="section-hint">Start with the domain you send the most mail from — you can add more later.</p>
        <AddDomainForm />
        <StepNav onBack={onBack} />
      </div>
    );
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
            fontSize: "0.83rem",
            display: "grid",
            gap: "0.35rem",
          }}
        >
          <CopyValue label="Host/name" value={domain.verification_record_name} />
          <span>
            <span className="muted">Type</span> <code>TXT</code>
          </span>
          <CopyValue label="Value" value={domain.verification_token} />
          {verifyDomain.isSuccess && !verifyDomain.data.verified && (
            <p style={{ color: "var(--critical-text)", marginTop: "0.2rem", marginBottom: 0 }}>
              Not found yet — DNS changes can take a few minutes to propagate.
            </p>
          )}
          <p className="muted" style={{ marginTop: "0.2rem", marginBottom: 0 }}>
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

      <StepNav onBack={onBack} onNext={verified ? onNext : undefined} />
    </div>
  );
}

function SignInStep({ onBack, onNext }: { onBack: () => void; onNext: () => void }) {
  const { data } = useSignInSettings();
  return (
    <div>
      <h2 style={{ marginTop: 0 }}>How people sign in</h2>
      <p className="section-hint">
        Let your team sign in with Microsoft, Google Workspace or your own OpenID Connect provider. Local accounts keep
        working next to it, and you can change this later in Settings → Sign-in.
      </p>
      <SignInTab />
      <div className="chip-row" style={{ marginTop: "1.25rem" }}>
        <button className="btn btn--ghost btn--sm" onClick={onBack}>
          <ArrowLeft />
          Back
        </button>
        {data?.provider && data.provider !== "none" ? (
          <button className="btn btn--primary btn--sm" onClick={onNext}>
            Continue
            <ArrowRight />
          </button>
        ) : (
          <button className="btn btn--secondary btn--sm" onClick={onNext}>
            Only local accounts for now
            <ArrowRight />
          </button>
        )}
      </div>
    </div>
  );
}

// Nothing is created until the admin picks: their own mailbox, a hosted
// address for the focus domain, or both.
function MailboxStep({ domain, onBack, onNext }: { domain: Domain | undefined; onBack: () => void; onNext: () => void }) {
  // Same query key/options MailboxConnectionSection uses below, so
  // react-query dedupes into one shared fetch/poll rather than two.
  const { data: connection } = useMailboxConnection();
  const { data: status } = useOnboardingStatus();
  const queryClient = useQueryClient();
  const [showOwn, setShowOwn] = useState(false);
  const [created, setCreated] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const hosted = created ?? domain?.hosted_report_address ?? null;
  const generate = useGenerateHostedReportAddress(
    domain?.id ?? "",
    (address) => {
      setCreated(address);
      setError(null);
      // So has_mailbox and the domain's address are current when the admin
      // comes back to this step or moves on.
      void queryClient.invalidateQueries({ queryKey: queryKeys.domains.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.onboarding.status });
    },
    (err) => setError(err instanceof ApiError ? err.message : "couldn't create a hosted address"),
  );
  const hostedOffered = !!status?.hosted_mailbox_ready;
  const done = !!connection || !!hosted;

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Where reports arrive</h2>
      <p className="section-hint">
        DMARC reports are emailed by mail providers. Read them from a mailbox of your own (Microsoft 365, Google
        Workspace or IMAP){hostedOffered ? ", use an address hosted by YetAnotherDmarcTool, or both" : ""}.
      </p>
      <div className="chip-row">
        {!connection && !showOwn && (
          <button className="btn btn--secondary btn--sm" onClick={() => setShowOwn(true)}>
            Use my own mailbox
          </button>
        )}
        {hostedOffered && !hosted && domain && (
          <button className="btn btn--secondary btn--sm" onClick={() => generate.mutate()} disabled={generate.isPending}>
            Use a YetAnotherDmarcTool-hosted address
          </button>
        )}
      </div>
      {(connection || showOwn) && <MailboxConnectionSection canManage={true} />}
      {hosted && (
        <div className="alert alert--good" style={{ marginTop: "0.75rem" }}>
          Hosted address for {domain?.name}: <CopyValue value={hosted} />
        </div>
      )}
      {error && <div className="alert alert--critical">{error}</div>}
      {!hostedOffered && !connection && <NoReportMailboxNotice />}
      <StepNav onBack={onBack} onNext={done ? onNext : undefined} />
    </div>
  );
}

function BaselineStep({
  domain,
  onBack,
  onVerify,
  onNext,
}: {
  domain: Domain | undefined;
  onBack: () => void;
  onVerify: () => void;
  onNext: () => void;
}) {
  const [ranOnce, setRanOnce] = useState(false);
  const recheck = useRecheckDns(domain?.id ?? "", () => setRanOnce(true));

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
            onClick={onVerify}
            style={{ display: "inline-flex", verticalAlign: "-2px" }}
          >
            back to Domain
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
        Checks SPF, DKIM, DMARC, MX, STARTTLS, MTA-STS, TLS-RPT, and DANE. Individual warnings don't block setup.
      </p>

      {!ranOnce && (
        <button className="btn btn--primary btn--sm" onClick={() => recheck.mutate()} disabled={recheck.isPending}>
          <RefreshCw />
          {recheck.isPending ? "Checking…" : "Run baseline check"}
        </button>
      )}

      {ranOnce && (
        <>
          <div className="alert alert--good">
            Baseline created — {counts.pass} passing, {counts.warn} warnings, {counts.fail} failing, {counts.error} couldn't be
            checked. After setup, the domain's DNS checks page explains each result and how to fix it.
          </div>
          <BaselineResults results={results} />
        </>
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

// The results right here in the step, rather than a link to the domain
// page: leaving the wizard halfway used to skip its remaining steps.
const RESULT_ORDER: Record<string, number> = { fail: 0, error: 1, warn: 2, pass: 3 };
const RESULT_BADGE: Record<string, { className: string; label: string }> = {
  fail: { className: "badge--critical", label: "failing" },
  error: { className: "badge--neutral", label: "couldn't check" },
  warn: { className: "badge--warning", label: "warning" },
  pass: { className: "badge--good", label: "passing" },
};

function BaselineResults({ results }: { results: CheckResult[] }) {
  const sorted = [...results].sort((a, b) => (RESULT_ORDER[a.status] ?? 9) - (RESULT_ORDER[b.status] ?? 9));
  return (
    <ul style={{ listStyle: "none", padding: 0, margin: "0.75rem 0 0", display: "grid", gap: "0.45rem" }}>
      {sorted.map((r) => {
        const badge = RESULT_BADGE[r.status] ?? { className: "badge--neutral", label: r.status };
        return (
          <li key={r.id} style={{ display: "flex", gap: "0.6rem", alignItems: "baseline", flexWrap: "wrap" }}>
            <span className={`badge ${badge.className}`} style={{ minWidth: "6.5rem", justifyContent: "center" }}>
              {badge.label}
            </span>
            <strong style={{ fontSize: "0.9rem" }}>
              {CHECK_LABELS[r.check_type] ?? r.check_type}
              {r.subject ? ` (${r.subject})` : ""}
            </strong>
            <span className="muted" style={{ fontSize: "0.88rem", minWidth: 0, flex: "1 1 18rem" }}>
              {r.summary}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

function ReportingStep({ domain, onBack, onNext }: { domain: Domain | undefined; onBack: () => void; onNext: () => void }) {
  const [showBuilder, setShowBuilder] = useState(false);
  const { data, isLoading } = useRuaCheck(domain?.id);
  const noReportMailbox = useNoReportMailbox();

  if (!domain) {
    return <p className="muted">No verified domain yet — go back and verify one first.</p>;
  }

  if (noReportMailbox) {
    return (
      <div>
        <h2 style={{ marginTop: 0 }}>Make sure reports reach this dashboard</h2>
        <NoReportMailboxNotice />
        <StepNav onBack={onBack} onNext={onNext} />
      </div>
    );
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
  const { data: connection, isLoading: mailboxLoading } = useMailboxConnection();
  const noReportMailbox = useNoReportMailbox();
  const { data: status } = useOnboardingStatus();
  const complete = useCompleteOnboarding();

  // Reaching this last step finishes the wizard: the Overview stops sending
  // org admins back here.
  useEffect(() => {
    if (status?.user_role === "org_admin" && !status.wizard_completed && complete.isIdle) complete.mutate();
  }, [status, complete]);

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>Waiting for your first report</h2>
      {noReportMailbox && <NoReportMailboxNotice />}
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
        <Link to="/settings/domains" className="chip-row" style={{ textDecoration: "none" }}>
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
