import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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

export default function AdminOrganizations() {
  const { admin, refetch: refetchAdmin } = useAdminAuth();
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

  async function handleLogout() {
    // Only a "local" session has anything for /admin/logout to revoke — an
    // operator_org admin is really just using their normal dashboard login,
    // so signing out of *that* belongs on the main Shell header, not here.
    if (admin?.auth_type === "local") {
      await api.post("/admin/logout");
      refetchAdmin();
    }
  }

  return (
    <main style={{ fontFamily: "system-ui, sans-serif", padding: "2rem", maxWidth: 1000, margin: "0 auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <h1>Organizations</h1>
        <div>
          <Link to="/admin/job-runs" style={{ marginRight: "1rem" }}>
            Job runs
          </Link>
          <span style={{ marginRight: "1rem" }}>
            {admin?.email}
            {admin?.auth_type === "operator_org" && (
              <span style={{ color: "#6b7280", fontSize: "0.8rem" }}> (via your org login)</span>
            )}
          </span>
          {admin?.auth_type === "local" ? (
            <button onClick={handleLogout}>Sign out</button>
          ) : (
            <Link to="/">&larr; Back to dashboard</Link>
          )}
        </div>
      </div>

      {admin?.auth_type === "local" && <ChangePassword />}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          createOrg.mutate();
        }}
        style={{ display: "flex", gap: "0.5rem", margin: "1rem 0" }}
      >
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Organization name" required />
        <input
          value={tenantId}
          onChange={(e) => setTenantId(e.target.value)}
          placeholder="Entra tenant ID (optional)"
          style={{ width: "260px" }}
        />
        <button type="submit" disabled={createOrg.isPending}>
          Create organization
        </button>
      </form>
      <p style={{ color: "#6b7280", fontSize: "0.85rem", marginTop: "-0.5rem" }}>
        Providing the tenant ID now activates the org immediately — the client can then sign in, approve
        consent, and set their own mailbox from inside their dashboard, no further steps needed here.
      </p>
      {error && <p style={{ color: "#b91c1c" }}>{error}</p>}

      {isLoading && <p>Loading…</p>}
      {(orgs ?? []).map((org) => (
        <OrgCard key={org.id} org={org} />
      ))}
    </main>
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

  return (
    <div style={{ border: "1px solid #e5e7eb", borderRadius: 8, padding: "1rem", marginBottom: "1rem" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <strong style={{ fontSize: "1.1rem" }}>{org.name}</strong>
        <select value={org.status} onChange={(e) => updateOrg.mutate({ status: e.target.value as AdminOrganization["status"] })}>
          <option value="pending_setup">pending_setup</option>
          <option value="active">active</option>
          <option value="suspended">suspended</option>
        </select>
      </div>

      <div style={{ display: "flex", gap: "3rem", marginTop: "0.75rem" }}>
        <div>
          <div style={{ fontSize: "0.8rem", color: "#6b7280" }}>Entra tenant ID</div>
          <div style={{ display: "flex", gap: "0.4rem", marginTop: "0.25rem" }}>
            <input
              value={tenantId}
              onChange={(e) => setTenantId(e.target.value)}
              placeholder="tenant GUID"
              style={{ width: "260px" }}
            />
            <button onClick={() => updateOrg.mutate({ entra_tenant_id: tenantId || null })}>Save</button>
          </div>
        </div>

        <div style={{ flex: 1 }}>
          <div style={{ fontSize: "0.8rem", color: "#6b7280" }}>Mailbox</div>
          <div style={{ display: "flex", gap: "0.4rem", marginTop: "0.25rem" }}>
            <input
              value={mailbox}
              onChange={(e) => setMailbox(e.target.value)}
              placeholder="dmarc-reports@org.com"
              style={{ width: "220px" }}
            />
            <button
              onClick={() => setMailboxConnection.mutate({ mailbox_address: mailbox })}
              disabled={!mailbox || setMailboxConnection.isPending}
            >
              Save
            </button>
          </div>
          {connection && (
            <div style={{ marginTop: "0.4rem", fontSize: "0.85rem" }}>
              Consent:{" "}
              <span
                style={{
                  color:
                    connection.consent_status === "granted"
                      ? "#166534"
                      : connection.consent_status === "revoked"
                        ? "#991b1b"
                        : "#92400e",
                  fontWeight: 600,
                }}
              >
                {connection.consent_status}
              </span>
              {connection.consent_status !== "granted" ? (
                <button
                  style={{ marginLeft: "0.5rem" }}
                  onClick={() =>
                    setMailboxConnection.mutate({ mailbox_address: connection.mailbox_address, consent_status: "granted" })
                  }
                >
                  Mark granted
                </button>
              ) : (
                <button
                  style={{ marginLeft: "0.5rem" }}
                  onClick={() =>
                    setMailboxConnection.mutate({ mailbox_address: connection.mailbox_address, consent_status: "revoked" })
                  }
                >
                  Revoke
                </button>
              )}
              <div style={{ color: "#6b7280", marginTop: "0.15rem" }}>
                {connection.last_sync_at
                  ? `Last synced ${new Date(connection.last_sync_at).toLocaleString()} (${connection.last_sync_status})`
                  : "Never synced yet"}
                {connection.last_sync_status === "error" && connection.last_sync_error && (
                  <div style={{ color: "#b91c1c" }}>{connection.last_sync_error}</div>
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
        <button onClick={() => setOpen(true)}>Change password</button>
      </p>
    );
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        change.mutate();
      }}
      style={{ display: "flex", gap: "0.5rem", alignItems: "center", margin: "0.5rem 0 1rem" }}
    >
      <input
        type="password"
        placeholder="current password"
        value={currentPassword}
        onChange={(e) => setCurrentPassword(e.target.value)}
        required
      />
      <input
        type="password"
        placeholder="new password (12+ chars)"
        value={newPassword}
        onChange={(e) => setNewPassword(e.target.value)}
        required
        minLength={12}
      />
      <button type="submit" disabled={change.isPending}>
        Save
      </button>
      <button type="button" onClick={() => setOpen(false)}>
        Cancel
      </button>
      {error && <span style={{ color: "#b91c1c" }}>{error}</span>}
      {success && <span style={{ color: "#166534" }}>Password changed.</span>}
    </form>
  );
}
