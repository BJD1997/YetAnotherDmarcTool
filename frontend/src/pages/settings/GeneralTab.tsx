import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, useOutletContext } from "react-router-dom";
import { ApiError } from "../../api/client";
import type { Organization, RatingWindowDays, ReportSenderCheck, SpfAllQualifierMode } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import { ASK_AI_EXPLANATION, AskAiToggle } from "../../components/settings/AskAiToggle";
import MailboxConnectionSection from "../../components/settings/MailboxConnectionSection";
import { useDomains } from "../../hooks/useDomains";
import { useMailboxConnection } from "../../hooks/useMailboxConnection";
import { useOnboardingStatus } from "../../hooks/useOnboarding";
import { useUpdateOrganization } from "../../hooks/useOrganization";
import { useRemoveTrustedReporter, useTrustedReporters } from "../../hooks/useTrustedReporters";

const SPF_MODES: { key: SpfAllQualifierMode; label: string; description: string }[] = [
  {
    key: "strict",
    label: "Strict",
    description: "-all is always recommended. The traditional advice — treats SPF's own hardfail as the goal.",
  },
  {
    key: "conditional",
    label: "Conditional",
    description:
      "~all is recommended instead for a sending domain once its own DMARC policy is quarantine/reject — at that point DMARC " +
      "is already the enforcement, so -all only risks bouncing relayed mail at the SMTP level before DKIM/DMARC are evaluated.",
  },
];

const SENDER_CHECKS: { key: ReportSenderCheck; label: string; description: string }[] = [
  {
    key: "standard",
    label: "Standard (recommended)",
    description:
      "Counts reports from known mail providers (Google, Microsoft, Yahoo, Mail.ru) and reporters you trust. Leaves out " +
      "reports that failed DMARC, came from someone other than the reporter they name, or were sent from inside your " +
      "Microsoft 365 organization.",
  },
  {
    key: "strict",
    label: "Strict",
    description:
      "Like Standard, but the sender must also have passed DMARC. Drops genuine reports from senders that have no " +
      "DMARC record.",
  },
  {
    key: "off",
    label: "Off",
    description: "Counts every report. Nothing is left out, but anyone who knows your reporting address can add fake data.",
  },
];

export default function GeneralTab() {
  const org = useOutletContext<Organization>();
  const { user } = useAuth();
  const canManage = user?.role === "org_admin";
  const { data: domains } = useDomains();
  const { data: connection } = useMailboxConnection();
  // An existing connection always stays visible; the hint only replaces the
  // forms for an org that has neither a verified domain nor a mailbox yet.
  const hasVerifiedDomain = domains === undefined || (domains ?? []).some((d) => d.verification_status === "verified");
  const showMailbox = hasVerifiedDomain || !!connection;

  return (
    <section>
      {/* Every organization connects one mailbox of its own: Microsoft 365
          (with a tenant test when it doesn't sign in with Microsoft) or any
          IMAP mailbox. */}
      <h3 className="section-title">Mailbox connection</h3>
      <p className="section-hint">The shared mailbox this organization's DMARC/TLS-RPT reports arrive at.</p>
      {showMailbox ? (
        <MailboxConnectionSection canManage={canManage} />
      ) : (
        <div className="alert alert--warning">
          Verify a domain first: the report mailbox has to be on one of your verified domains.{" "}
          <Link to="/settings/domains">Settings → Domains</Link>
        </div>
      )}

      {canManage && (
        <>
          <hr className="divider" />
          <h3 className="section-title">SPF "all" recommendation</h3>
          <p className="section-hint">How the SPF check scores a record ending in -all (hardfail) vs ~all (softfail).</p>
          <SpfModeSection org={org} />

          <hr className="divider" />
          <h3 className="section-title">Rating period</h3>
          <p className="section-hint">
            How far back each domain's grade, failing-message count and sender list look. Shorter reacts faster to a fix;
            longer is steadier.
          </p>
          <RatingWindowSection org={org} />

          <hr className="divider" />
          <h3 className="section-title">Ask AI</h3>
          <p className="section-hint">{ASK_AI_EXPLANATION}</p>
          <AskAiToggle org={org} />

          <hr className="divider" />
          <h3 className="section-title">Report sender check</h3>
          <p className="section-hint">
            Anyone can email a report to your reporting address. This decides which reports count, based on how their
            sender checks out. Left-out reports are kept and listed on each domain's report pages. Changing this also
            applies to reports you've already received.
          </p>
          <SenderCheckSection org={org} />
          <TrustedReportersSection />

          <hr className="divider" />
          <h3 className="section-title">Hosted reporting mailbox</h3>
          <p className="section-hint">A YetAnotherDmarcTool-hosted rua= address for a domain, alongside or in place of your own mailbox.</p>
          <HostedMailboxSection />
        </>
      )}
    </section>
  );
}

const RATING_WINDOWS: RatingWindowDays[] = [30, 60, 90, 180];

function RatingWindowSection({ org }: { org: Organization }) {
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const setWindow = useUpdateOrganization(
    () => {
      setError(null);
      // Grades, counts and the Senders list all change with it.
      void queryClient.invalidateQueries();
    },
    (err) => setError(err instanceof ApiError ? err.message : "failed to save"),
  );

  return (
    <div style={{ display: "grid", gap: "0.5rem", maxWidth: 260 }}>
      <select
        aria-label="Rating period"
        className="input"
        value={org.rating_window_days}
        disabled={setWindow.isPending}
        onChange={(e) => setWindow.mutate({ name: org.name, rating_window_days: Number(e.target.value) as RatingWindowDays })}
      >
        {RATING_WINDOWS.map((d) => (
          <option key={d} value={d}>
            Last {d} days{d === 90 ? " (default)" : ""}
          </option>
        ))}
      </select>
      {error && <div className="alert alert--critical" style={{ margin: 0 }}>{error}</div>}
    </div>
  );
}

function SenderCheckSection({ org }: { org: Organization }) {
  const [error, setError] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const setCheck = useUpdateOrganization(
    () => {
      setError(null);
      // Reports already received are re-evaluated, so every report view changes.
      void queryClient.invalidateQueries();
    },
    (err) => setError(err instanceof ApiError ? err.message : "failed to save"),
  );

  return (
    <div className="card" style={{ padding: "1rem", display: "grid", gap: "0.6rem" }} role="radiogroup" aria-label="Report sender check">
      {SENDER_CHECKS.map((c) => (
        <label key={c.key} style={{ display: "flex", gap: "0.6rem", alignItems: "flex-start", cursor: "pointer" }}>
          <input
            type="radio"
            name="report-sender-check"
            id={`report-sender-check-${c.key}`}
            checked={org.report_sender_check === c.key}
            disabled={setCheck.isPending}
            onChange={() => setCheck.mutate({ name: org.name, report_sender_check: c.key })}
            style={{ marginTop: "0.25rem" }}
          />
          <span>
            <strong style={{ fontWeight: 600 }}>{c.label}</strong>
            <span className="section-hint" style={{ display: "block", margin: 0 }}>
              {c.description}
            </span>
          </span>
        </label>
      ))}
      {error && <div className="alert alert--critical" style={{ margin: 0 }}>{error}</div>}
    </div>
  );
}

function TrustedReportersSection() {
  const [error, setError] = useState<string | null>(null);
  const { data } = useTrustedReporters();
  const remove = useRemoveTrustedReporter((err) => setError(err instanceof ApiError ? err.message : "failed to remove"));
  if (!data) return null;

  return (
    <div style={{ marginTop: "0.8rem" }}>
      <strong style={{ fontWeight: 600 }}>Reporters you trust</strong>
      <p className="section-hint" style={{ margin: "0.2rem 0 0.5rem" }}>
        Reports can claim anything, so only reports from known mail providers count by default. Trust another reporter
        from a domain's left-out reports list.
      </p>
      {data.length === 0 ? (
        <p className="section-hint" style={{ margin: 0 }}>None yet.</p>
      ) : (
        <div className="chip-row">
          {data.map((r) => (
            <span key={r.domain} className="badge">
              {r.domain}
              <button
                type="button"
                className="btn btn--ghost btn--sm"
                aria-label={`Stop trusting ${r.domain}`}
                disabled={remove.isPending}
                onClick={() => remove.mutate(r.domain)}
              >
                Remove
              </button>
            </span>
          ))}
        </div>
      )}
      {error && <div className="alert alert--critical" style={{ marginTop: "0.5rem" }}>{error}</div>}
    </div>
  );
}

function SpfModeSection({ org }: { org: Organization }) {
  const [error, setError] = useState<string | null>(null);

  const setMode = useUpdateOrganization(
    () => setError(null),
    (err) => setError(err instanceof ApiError ? err.message : "failed to save"),
  );

  return (
    <div className="card" style={{ padding: "1rem" }}>
      <div className="chip-row">
        {SPF_MODES.map((m) => (
          <button
            key={m.key}
            className="btn btn--ghost btn--sm"
            style={org.spf_all_qualifier_mode === m.key ? { background: "var(--accent-wash)", color: "var(--accent)" } : undefined}
            disabled={setMode.isPending}
            onClick={() => setMode.mutate({ name: org.name, spf_all_qualifier_mode: m.key })}
          >
            {m.label}
          </button>
        ))}
      </div>
      <p className="section-hint" style={{ marginTop: "0.6rem", marginBottom: 0 }}>
        {SPF_MODES.find((m) => m.key === org.spf_all_qualifier_mode)?.description}
      </p>
      {error && <div className="alert alert--critical" style={{ marginTop: "0.5rem" }}>{error}</div>}
    </div>
  );
}

function HostedMailboxSection() {
  const { data: status } = useOnboardingStatus();
  return (
    <div className="card" style={{ padding: "1rem" }}>
      <p className="section-hint" style={{ margin: 0 }}>
        {status && !status.hosted_mailbox_ready
          ? "This server doesn't offer hosted reporting addresses."
          : "Available on this server: each domain can get a YetAnotherDmarcTool-hosted reporting address, as well as or instead of your own mailbox above. Add one from a domain's DMARC policy builder."}
      </p>
    </div>
  );
}
