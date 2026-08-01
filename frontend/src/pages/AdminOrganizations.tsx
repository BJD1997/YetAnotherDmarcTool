import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { api, ApiError } from "../api/client";
import { useAdminAuth } from "../auth/AdminAuthContext";

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
  status: "pending_setup" | "active" | "suspended";
  created_at: string;
  mailbox_connection: MailboxConnectionInfo | null;
  entra_consent_urls: EntraConsentUrls | null;
}

const STATUS_ROLE: Record<AdminOrganization["status"], "good" | "warning" | "serious"> = {
  active: "good",
  pending_setup: "warning",
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
          <option value="pending_setup">pending_setup</option>
          <option value="active">active</option>
          <option value="suspended">suspended</option>
        </select>
      </div>
      <div style={{ marginTop: "0.4rem" }}>
        <span className={`badge badge--${STATUS_ROLE[org.status]}`}>{org.status.replace("_", " ")}</span>
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
