import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useOutletContext } from "react-router-dom";
import { ApiError } from "../../api/client";
import type { Organization, RatingWindowDays, ReportSenderCheck, SpfAllQualifierMode } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import MailboxConnectionSection from "../../components/settings/MailboxConnectionSection";
import { useUpdateOrganization } from "../../hooks/useOrganization";

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
      "Leaves out reports that failed DMARC, came from someone other than the reporter they name, or were sent from inside " +
      "your Microsoft 365 organization. Genuine reports only drop out if they fail DMARC, which is rare.",
  },
  {
    key: "strict",
    label: "Strict",
    description:
      "Counts only reports whose sender passed DMARC. Best protection against fake reports, but also drops genuine " +
      "reports from senders that have no DMARC record.",
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

  return (
    <section>
      {/* Local-auth orgs (no entra_tenant_id) have no Entra tenant to grant
          Mail Access consent from, so this section is never functional for
          them — "grant access" has no links to show, and the mailbox-address
          form is a dead end. They get a hosted address per domain instead
          (see the "Hosted reporting mailbox" section below), which already
          explains their situation — no need to also show this. */}
      {org.entra_tenant_id && (
        <>
          <h3 className="section-title">Mailbox connection</h3>
          <p className="section-hint">The shared mailbox this organization's DMARC/TLS-RPT reports arrive at.</p>
          <MailboxConnectionSection canManage={canManage} />
        </>
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
          <h3 className="section-title">Report sender check</h3>
          <p className="section-hint">
            Anyone can email a report to your reporting address. This decides which reports count, based on how their
            sender checks out. Left-out reports are kept and listed on each domain's report pages. Changing this also
            applies to reports you've already received.
          </p>
          <SenderCheckSection org={org} />

          <hr className="divider" />
          <h3 className="section-title">Hosted reporting mailbox</h3>
          <p className="section-hint">A YetAnotherDmarcTool-hosted rua= address, for domains with no mailbox of their own to dedicate.</p>
          <HostedMailboxSection org={org} />
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

function HostedMailboxSection({ org }: { org: Organization }) {
  const [error, setError] = useState<string | null>(null);

  const setOptIn = useUpdateOrganization(
    () => setError(null),
    (err) => setError(err instanceof ApiError ? err.message : "failed to save"),
  );

  // Local-auth orgs have no Entra tenant to grant Mail Access consent from
  // — a hosted mailbox is their only way to receive reports at all, so
  // there's nothing to toggle for them (see _hosted_mailbox_available in
  // app/routers/domains.py).
  if (!org.entra_tenant_id) {
    return (
      <div className="card" style={{ padding: "1rem" }}>
        <p className="section-hint" style={{ margin: 0 }}>
          Always available — your organization signs in without Microsoft Entra, so a YetAnotherDmarcTool-hosted mailbox is your only
          reporting option.
        </p>
      </div>
    );
  }

  return (
    <div className="card" style={{ padding: "1rem" }}>
      <div className="chip-row">
        {[
          { key: false, label: "Off" },
          { key: true, label: "On" },
        ].map((opt) => (
          <button
            key={String(opt.key)}
            className="btn btn--ghost btn--sm"
            style={org.hosted_mailbox_opt_in === opt.key ? { background: "var(--accent-wash)", color: "var(--accent)" } : undefined}
            disabled={setOptIn.isPending}
            onClick={() => setOptIn.mutate({ name: org.name, hosted_mailbox_opt_in: opt.key })}
          >
            {opt.label}
          </button>
        ))}
      </div>
      <p className="section-hint" style={{ marginTop: "0.6rem", marginBottom: 0 }}>
        Off by default since your organization can connect its own mailbox. Turn on to also allow generating
        YetAnotherDmarcTool-hosted addresses per domain from the Policy Builder.
      </p>
      {error && <div className="alert alert--critical" style={{ marginTop: "0.5rem" }}>{error}</div>}
    </div>
  );
}
