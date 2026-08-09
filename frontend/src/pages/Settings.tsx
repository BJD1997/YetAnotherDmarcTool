import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { DetectedDomain, Domain, Organization, SpfAllQualifierMode } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import MailboxConnectionSection from "../components/settings/MailboxConnectionSection";
import AddDomainForm from "../components/settings/AddDomainForm";
import SignInEventsSection from "../components/settings/SignInEventsSection";

export default function Settings() {
  const { user } = useAuth();
  const canManage = user?.role === "org_admin";
  const { data: org } = useQuery({
    queryKey: ["organization", "current"],
    queryFn: () => api.get<Organization>("/organizations/current"),
  });

  return (
    <section>
      <div className="page-header">
        <h1>Settings</h1>
      </div>

      {/* Local-auth orgs (no entra_tenant_id) have no Entra tenant to grant
          Mail Access consent from, so this section is never functional for
          them — "grant access" has no links to show, and the mailbox-address
          form is a dead end. They get a hosted address per domain instead
          (see the "Hosted reporting mailbox" section below), which already
          explains their situation — no need to also show this. */}
      {org?.entra_tenant_id && (
        <>
          <h3 className="section-title">Mailbox connection</h3>
          <p className="section-hint">The shared mailbox this organization's DMARC/TLS-RPT reports arrive at.</p>
          <MailboxConnectionSection canManage={canManage} />
        </>
      )}

      {canManage && (
        <>
          <hr className="divider" />
          <h3 className="section-title">Add a domain</h3>
          <p className="section-hint">Domains and subdomains to monitor. Manage the full list from the Domains page.</p>
          <AddDomainForm />
          <DetectedDomains />

          <hr className="divider" />
          <h3 className="section-title">SPF "all" recommendation</h3>
          <p className="section-hint">
            How the SPF check scores a record ending in -all (hardfail) vs ~all (softfail).
          </p>
          <SpfModeSection />

          <hr className="divider" />
          <h3 className="section-title">Hosted reporting mailbox</h3>
          <p className="section-hint">A YetAnotherDmarcTool-hosted rua= address, for domains with no mailbox of their own to dedicate.</p>
          <HostedMailboxSection />

          <hr className="divider" />
          <h3 className="section-title">Sign-in activity</h3>
          <p className="section-hint">Successful and failed sign-ins to your organization, most recent first.</p>
          <SignInEventsSection />
        </>
      )}
    </section>
  );
}

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

function SpfModeSection() {
  const queryClient = useQueryClient();
  const { data: org } = useQuery({
    queryKey: ["organization", "current"],
    queryFn: () => api.get<Organization>("/organizations/current"),
  });
  const [error, setError] = useState<string | null>(null);

  const setMode = useMutation({
    mutationFn: (mode: SpfAllQualifierMode) =>
      api.patch<Organization>("/organizations/current", { name: org?.name, spf_all_qualifier_mode: mode }),
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["organization", "current"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "failed to save"),
  });

  if (!org) return null;

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

function HostedMailboxSection() {
  const queryClient = useQueryClient();
  const { data: org } = useQuery({
    queryKey: ["organization", "current"],
    queryFn: () => api.get<Organization>("/organizations/current"),
  });
  const [error, setError] = useState<string | null>(null);

  const setOptIn = useMutation({
    mutationFn: (optIn: boolean) =>
      api.patch<Organization>("/organizations/current", { name: org?.name, hosted_mailbox_opt_in: optIn }),
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["organization", "current"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "failed to save"),
  });

  if (!org) return null;

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

function DetectedDomains() {
  const queryClient = useQueryClient();
  const { data: detected, isLoading } = useQuery({
    queryKey: ["detected-domains"],
    queryFn: () => api.get<DetectedDomain[]>("/dmarc/detected-domains"),
  });
  const [error, setError] = useState<string | null>(null);

  const addDetected = useMutation({
    mutationFn: (item: DetectedDomain) =>
      api.post<Domain & { reattributed_reports: number; reattributed_records: number }>("/domains", {
        name: item.name,
        parent_domain_id: item.suggested_parent_id,
      }),
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["domains"] });
      queryClient.invalidateQueries({ queryKey: ["detected-domains"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "failed to add domain"),
  });

  const dismissDetected = useMutation({
    mutationFn: (item: DetectedDomain) => api.post<void>(`/dmarc/detected-domains/${encodeURIComponent(item.name)}/dismiss`),
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["detected-domains"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "failed to dismiss"),
  });

  if (isLoading || !detected || detected.length === 0) return null;

  return (
    <div style={{ marginTop: "1.25rem" }}>
      <div className="section-title" style={{ fontSize: "0.85rem" }}>
        Detected domains
      </div>
      <p className="section-hint">Your mailbox is receiving DMARC/TLS reports for these domains, but none of them are added yet.</p>
      {error && <div className="alert alert--critical">{error}</div>}
      <div className="card" style={{ padding: 0 }}>
        {detected.map((item, i) => (
          <div
            key={item.name}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              gap: "0.75rem",
              padding: "0.75rem 1rem",
              borderBottom: i < detected.length - 1 ? "1px solid var(--border)" : "none",
            }}
          >
            <div>
              <strong>{item.name}</strong>
              <span className="muted" style={{ marginLeft: "0.5rem", fontSize: "0.8rem" }}>
                {item.report_count} report{item.report_count === 1 ? "" : "s"}
                {item.message_volume > 0 && `, ${item.message_volume} messages`}
              </span>
              {item.relationship === "subdomain_of_registered" && (
                <span className="badge badge--good" style={{ marginLeft: "0.5rem" }}>
                  subdomain of {item.suggested_parent_name}
                </span>
              )}
              {item.relationship === "subdomain_of_detected" && (
                <div className="section-hint" style={{ margin: "0.15rem 0 0" }}>
                  looks like a subdomain of {item.suggested_parent_name}, which is also detected but not yet added —
                  consider adding that one first
                </div>
              )}
            </div>
            <div style={{ display: "flex", gap: "0.4rem", flexShrink: 0 }}>
              <button className="btn btn--secondary btn--sm" onClick={() => addDetected.mutate(item)} disabled={addDetected.isPending}>
                Add &amp; verify
              </button>
              <button
                className="btn btn--ghost btn--sm"
                title="Not mine — stop suggesting it"
                onClick={() => dismissDetected.mutate(item)}
                disabled={dismissDetected.isPending}
              >
                <X size={14} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
