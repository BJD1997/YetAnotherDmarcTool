import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useOutletContext } from "react-router-dom";
import { api, ApiError } from "../../api/client";
import type { Organization, SpfAllQualifierMode } from "../../api/types";
import { useAuth } from "../../auth/AuthContext";
import MailboxConnectionSection from "../../components/settings/MailboxConnectionSection";

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
          <h3 className="section-title">Hosted reporting mailbox</h3>
          <p className="section-hint">A YetAnotherDmarcTool-hosted rua= address, for domains with no mailbox of their own to dedicate.</p>
          <HostedMailboxSection org={org} />
        </>
      )}
    </section>
  );
}

function SpfModeSection({ org }: { org: Organization }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const setMode = useMutation({
    mutationFn: (mode: SpfAllQualifierMode) =>
      api.patch<Organization>("/organizations/current", { name: org.name, spf_all_qualifier_mode: mode }),
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["organization", "current"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "failed to save"),
  });

  return (
    <div className="card" style={{ padding: "1rem" }}>
      <div className="chip-row">
        {SPF_MODES.map((m) => (
          <button
            key={m.key}
            className="btn btn--ghost btn--sm"
            style={org.spf_all_qualifier_mode === m.key ? { background: "var(--accent-wash)", color: "var(--accent)" } : undefined}
            disabled={setMode.isPending}
            onClick={() => setMode.mutate(m.key)}
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
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const setOptIn = useMutation({
    mutationFn: (optIn: boolean) => api.patch<Organization>("/organizations/current", { name: org.name, hosted_mailbox_opt_in: optIn }),
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["organization", "current"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "failed to save"),
  });

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
            onClick={() => setOptIn.mutate(opt.key)}
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
