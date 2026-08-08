import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Plus, Trash2, UserPlus } from "lucide-react";
import { api, ApiError } from "../api/client";
import { useAdminAuth } from "../auth/AdminAuthContext";
import { ReportFreshnessValue } from "../components/overview/widgets";

interface MailboxConnectionInfo {
  id: string;
  mailbox_address: string;
  consent_status: "pending" | "granted" | "revoked";
  consent_granted_at: string | null;
  last_sync_at: string | null;
  last_sync_status: "success" | "error" | null;
  last_sync_error: string | null;
}

interface EntraConsentUrls {
  mail_access_consent_url: string;
  sso_consent_url: string;
}

interface AdminOrganization {
  id: string;
  name: string;
  entra_tenant_id: string | null;
  status: "active" | "suspended";
  is_operator: boolean;
  created_at: string;
  mailbox_connection: MailboxConnectionInfo | null;
  entra_consent_urls: EntraConsentUrls | null;
  domain_count: number;
  job_error_count_7d: number;
  last_report_at: string | null;
}

const STATUS_ROLE: Record<AdminOrganization["status"], "good" | "serious"> = {
  active: "good",
  suspended: "serious",
};

export default function AdminOrganizations() {
  const { admin } = useAdminAuth();
  const queryClient = useQueryClient();
  const { data: orgs, isLoading } = useQuery({
    queryKey: ["admin-organizations"],
    queryFn: () => api.get<AdminOrganization[]>("/admin/organizations"),
  });

  const [name, setName] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const createOrg = useMutation({
    mutationFn: () =>
      api.post<AdminOrganization>("/admin/organizations", { name, entra_tenant_id: tenantId || null }),
    onSuccess: () => {
      setName("");
      setTenantId("");
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["admin-organizations"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "failed to create organization"),
  });

  return (
    <section>
      <div className="page-header">
        <h1>Organizations</h1>
      </div>

      {admin?.auth_type === "local" && <ChangePassword />}

      <div className="card">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            createOrg.mutate();
          }}
          className="field-row"
        >
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Organization name" required />
          <input
            className="input"
            value={tenantId}
            onChange={(e) => setTenantId(e.target.value)}
            placeholder="Entra tenant ID (optional)"
            style={{ width: "260px" }}
          />
          <button type="submit" className="btn btn--primary" disabled={createOrg.isPending}>
            <Plus />
            Create organization
          </button>
        </form>
        <p className="section-hint" style={{ marginBottom: 0 }}>
          Providing the tenant ID now activates the org immediately — the client can then sign in, approve consent,
          and set their own mailbox from inside their dashboard, no further steps needed here.
        </p>
        {error && (
          <div className="alert alert--critical" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
            {error}
          </div>
        )}
      </div>

      {isLoading && <p className="muted">Loading…</p>}
      {(orgs ?? []).map((org) => (
        <OrgCard key={org.id} org={org} />
      ))}
    </section>
  );
}

function OrgCard({ org }: { org: AdminOrganization }) {
  const queryClient = useQueryClient();
  const [tenantId, setTenantId] = useState(org.entra_tenant_id ?? "");
  const [mailbox, setMailbox] = useState(org.mailbox_connection?.mailbox_address ?? "");

  const updateOrg = useMutation({
    mutationFn: (body: Partial<Pick<AdminOrganization, "entra_tenant_id" | "status">>) =>
      api.patch<AdminOrganization>(`/admin/organizations/${org.id}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-organizations"] }),
  });

  const setMailboxConnection = useMutation({
    mutationFn: (body: { mailbox_address: string; consent_status?: string }) =>
      api.post(`/admin/organizations/${org.id}/mailbox-connection`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-organizations"] }),
  });

  const connection = org.mailbox_connection;
  const consentRole = connection
    ? connection.consent_status === "granted"
      ? "good"
      : connection.consent_status === "revoked"
        ? "critical"
        : "warning"
    : "neutral";

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <strong style={{ fontSize: "1.05rem" }}>{org.name}</strong>
        <select
          className="input"
          value={org.status}
          onChange={(e) => updateOrg.mutate({ status: e.target.value as AdminOrganization["status"] })}
        >
          <option value="active">active</option>
          <option value="suspended">suspended</option>
        </select>
      </div>
      <div style={{ marginTop: "0.4rem" }}>
        <span className={`badge badge--${STATUS_ROLE[org.status]}`}>{org.status}</span>
        {org.is_operator && <span className="badge badge--neutral" style={{ marginLeft: "0.4rem" }}>operator</span>}
      </div>

      <div className="chip-row" style={{ marginTop: "0.6rem", fontSize: "0.8rem" }}>
        <span className="muted">
          {org.domain_count} domain{org.domain_count === 1 ? "" : "s"}
        </span>
        <span className="muted">
          last report:{" "}
          {org.last_report_at ? (
            <>
              <ReportFreshnessValue hours={(Date.now() - new Date(org.last_report_at).getTime()) / 3_600_000} /> ago
            </>
          ) : (
            "never"
          )}
        </span>
        {org.job_error_count_7d > 0 && (
          <span className="badge badge--critical">
            {org.job_error_count_7d} job error{org.job_error_count_7d === 1 ? "" : "s"} (7d)
          </span>
        )}
      </div>

      <div style={{ display: "flex", gap: "3rem", marginTop: "1rem", flexWrap: "wrap" }}>
        <div>
          <div className="muted" style={{ fontSize: "0.78rem" }}>
            Entra tenant ID
          </div>
          <div className="field-row" style={{ marginTop: "0.3rem" }}>
            <input className="input" value={tenantId} onChange={(e) => setTenantId(e.target.value)} placeholder="tenant GUID" style={{ width: "260px" }} />
            <button className="btn btn--secondary btn--sm" onClick={() => updateOrg.mutate({ entra_tenant_id: tenantId || null })}>
              Save
            </button>
          </div>
        </div>

        <div style={{ flex: 1, minWidth: 260 }}>
          <div className="muted" style={{ fontSize: "0.78rem" }}>
            Mailbox
          </div>
          <div className="field-row" style={{ marginTop: "0.3rem" }}>
            <input className="input" value={mailbox} onChange={(e) => setMailbox(e.target.value)} placeholder="dmarc-reports@org.com" style={{ width: "220px" }} />
            <button
              className="btn btn--secondary btn--sm"
              onClick={() => setMailboxConnection.mutate({ mailbox_address: mailbox })}
              disabled={!mailbox || setMailboxConnection.isPending}
            >
              Save
            </button>
          </div>
          {connection && (
            <div style={{ marginTop: "0.5rem", fontSize: "0.85rem" }}>
              <span className={`badge badge--${consentRole}`}>{connection.consent_status}</span>
              {connection.consent_status !== "granted" ? (
                <button
                  className="btn btn--ghost btn--sm"
                  style={{ marginLeft: "0.5rem" }}
                  onClick={() => setMailboxConnection.mutate({ mailbox_address: connection.mailbox_address, consent_status: "granted" })}
                >
                  Mark granted
                </button>
              ) : (
                <button
                  className="btn btn--ghost btn--sm"
                  style={{ marginLeft: "0.5rem" }}
                  onClick={() => setMailboxConnection.mutate({ mailbox_address: connection.mailbox_address, consent_status: "revoked" })}
                >
                  Revoke
                </button>
              )}
              <div className="muted" style={{ marginTop: "0.3rem" }}>
                {connection.last_sync_at
                  ? `Last synced ${new Date(connection.last_sync_at).toLocaleString()} (${connection.last_sync_status})`
                  : "Never synced yet"}
                {connection.last_sync_status === "error" && connection.last_sync_error && (
                  <div style={{ color: "var(--critical-text)" }}>{connection.last_sync_error}</div>
                )}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* No consent-links block here anymore — dashboard SSO consent
          happens automatically at first sign-in (Microsoft handles it
          inline), and the Mail Access link is already surfaced inside the
          client's own portal once they're in (see MailboxConnectionStatusBanner
          in Domains.tsx), so there's nothing left for the platform admin to
          relay out-of-band. */}

      {!org.entra_tenant_id && !org.is_operator && <CreateLocalUser orgId={org.id} />}

      <DeleteOrgSection org={org} />
    </div>
  );
}

function CreateLocalUser({ orgId }: { orgId: string }) {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [setupLink, setSetupLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const createUser = useMutation({
    mutationFn: () => api.post<{ setup_link: string }>(`/admin/organizations/${orgId}/users`, { email }),
    onSuccess: (created) => {
      setEmail("");
      setError(null);
      setSetupLink(created.setup_link);
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "failed to create user"),
  });

  function copy() {
    if (!setupLink) return;
    navigator.clipboard.writeText(setupLink).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div style={{ marginTop: "1rem", paddingTop: "0.75rem", borderTop: "1px solid var(--border)" }}>
      <div className="muted" style={{ fontSize: "0.78rem", marginBottom: "0.3rem" }}>
        No Entra tenant — bootstrap this org's first user
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          createUser.mutate();
        }}
        className="field-row"
      >
        <input
          className="input"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="admin@client.com"
          style={{ width: "220px" }}
          required
        />
        <button type="submit" className="btn btn--secondary btn--sm" disabled={createUser.isPending}>
          <UserPlus />
          Generate setup link
        </button>
      </form>
      {error && (
        <div className="alert alert--critical" style={{ marginTop: "0.5rem", marginBottom: 0 }}>
          {error}
        </div>
      )}
      {setupLink && (
        <div className="field-row" style={{ marginTop: "0.5rem" }}>
          <code className="input" style={{ display: "inline-block" }}>
            {setupLink}
          </code>
          <button className="btn btn--secondary btn--sm" onClick={copy}>
            {copied ? <Check /> : <Copy />}
            {copied ? "Copied" : "Copy link"}
          </button>
        </div>
      )}
    </div>
  );
}

function DeleteOrgSection({ org }: { org: AdminOrganization }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [confirmText, setConfirmText] = useState("");

  const deleteOrg = useMutation({
    mutationFn: () => api.delete(`/admin/organizations/${org.id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["admin-organizations"] }),
  });

  if (org.is_operator) {
    return (
      <p className="muted" style={{ fontSize: "0.78rem", marginTop: "1rem", marginBottom: 0 }}>
        The operator organization can't be deleted from here.
      </p>
    );
  }

  if (!confirming) {
    return (
      <div style={{ marginTop: "1rem", paddingTop: "0.75rem", borderTop: "1px solid var(--border)" }}>
        <button className="btn btn--danger btn--sm" onClick={() => setConfirming(true)}>
          <Trash2 />
          Delete organization
        </button>
      </div>
    );
  }

  return (
    <div style={{ marginTop: "1rem", paddingTop: "0.75rem", borderTop: "1px solid var(--border)" }}>
      <div className="alert alert--critical" style={{ marginBottom: "0.6rem" }}>
        This permanently deletes <strong>{org.name}</strong> and everything tied to it — mailbox connection,
        domains, DKIM selectors, every DMARC/TLS-RPT/forensic report, users, and job history. This can't be undone.
        Type the organization name to confirm.
      </div>
      <div className="field-row">
        <input
          className="input"
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
          placeholder={org.name}
          style={{ width: "260px" }}
        />
        <button
          className="btn btn--danger btn--sm"
          disabled={confirmText !== org.name || deleteOrg.isPending}
          onClick={() => deleteOrg.mutate()}
        >
          {deleteOrg.isPending ? "Deleting…" : "Confirm delete"}
        </button>
        <button
          className="btn btn--ghost btn--sm"
          onClick={() => {
            setConfirming(false);
            setConfirmText("");
          }}
        >
          Cancel
        </button>
      </div>
      {deleteOrg.isError && (
        <div className="alert alert--critical" style={{ marginTop: "0.6rem", marginBottom: 0 }}>
          {deleteOrg.error instanceof ApiError ? deleteOrg.error.message : "failed to delete organization"}
        </div>
      )}
    </div>
  );
}

function ChangePassword() {
  const [open, setOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  const change = useMutation({
    mutationFn: () => api.post("/admin/change-password", { current_password: currentPassword, new_password: newPassword }),
    onSuccess: () => {
      setError(null);
      setSuccess(true);
      setCurrentPassword("");
      setNewPassword("");
    },
    onError: (err) => {
      setSuccess(false);
      setError(err instanceof ApiError ? err.message : "failed to change password");
    },
  });

  if (!open) {
    return (
      <p>
        <button className="btn btn--ghost btn--sm" onClick={() => setOpen(true)}>
          Change password
        </button>
      </p>
    );
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        change.mutate();
      }}
      className="field-row"
      style={{ margin: "0.5rem 0 1.25rem" }}
    >
      <input
        className="input"
        type="password"
        placeholder="current password"
        value={currentPassword}
        onChange={(e) => setCurrentPassword(e.target.value)}
        required
      />
      <input
        className="input"
        type="password"
        placeholder="new password (12+ chars)"
        value={newPassword}
        onChange={(e) => setNewPassword(e.target.value)}
        required
        minLength={12}
      />
      <button type="submit" className="btn btn--primary btn--sm" disabled={change.isPending}>
        Save
      </button>
      <button type="button" className="btn btn--ghost btn--sm" onClick={() => setOpen(false)}>
        Cancel
      </button>
      {error && <span style={{ color: "var(--critical-text)" }}>{error}</span>}
      {success && <span style={{ color: "var(--good-text)" }}>Password changed.</span>}
    </form>
  );
}
