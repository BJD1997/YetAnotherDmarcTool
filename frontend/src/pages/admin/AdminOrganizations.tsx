import { useEffect, useState } from "react";
import { Check, ChevronDown, ChevronRight, Copy, Plus, Trash2, UserPlus } from "lucide-react";
import { ApiError } from "../../api/client";
import { useAdminAuth } from "../../auth/AdminAuthContext";
import { LoadMoreButton } from "../../components/shared/LoadMoreButton";
import { ReportFreshnessValue } from "../../components/overview/widgets";
import { Stat } from "../../components/domain/shared";
import {
  useAdminOrganizations, useAdminOrgUsers, useAdminResetUser, useChangeAdminPassword, useCreateAdminOrganization,
  useCreateAdminUser, useDeleteAdminOrganization, useSetAdminMailboxConnection, useUpdateAdminOrganization,
  type AdminOrganization, type AdminOrganizationsPage, type AdminOrgUser,
} from "../../hooks/useAdmin";
import { useClipboardFeedback } from "../../hooks/useClipboardFeedback";

const STATUS_ROLE: Record<AdminOrganization["status"], "good" | "serious"> = {
  active: "good",
  suspended: "serious",
};

export default function AdminOrganizations() {
  const { admin } = useAdminAuth();
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  // Debounce: the input itself always reflects every keystroke immediately
  // (bound to searchInput below), but the query-triggering `search` state
  // only catches up 300ms after typing pauses, so a fast typist doesn't
  // fire a request per character.
  useEffect(() => {
    const timeout = setTimeout(() => setSearch(searchInput), 300);
    return () => clearTimeout(timeout);
  }, [searchInput]);
  const query = useAdminOrganizations(search);
  const pages = query.data?.pages ?? [];
  const orgs = pages.flatMap((p) => p.organizations);
  // The summary is computed server-side over the whole filtered set on
  // every page fetch, not per page — the first page's copy is as current
  // as any other.
  const summary = pages[0]?.summary;

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [tenantId, setTenantId] = useState("");
  const [createError, setCreateError] = useState<string | null>(null);

  const createOrg = useCreateAdminOrganization(
    () => { setName(""); setTenantId(""); setCreateError(null); },
    (err) => setCreateError(err instanceof ApiError ? err.message : "failed to create organization"),
  );

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
            createOrg.mutate({ name, entra_tenant_id: tenantId || null });
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
        {createError && (
          <div className="alert alert--critical" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
            {createError}
          </div>
        )}
      </div>

      <SummaryBar summary={summary} isLoading={query.isLoading} />

      <div className="card">
        <input
          className="input"
          placeholder="Search organizations by name"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />
      </div>

      {query.isLoading && <p className="muted">Loading…</p>}
      {query.isSuccess && orgs.length === 0 && <p className="empty-state">No organizations match your search.</p>}

      {orgs.length > 0 && (
        <div className="card" style={{ padding: 0 }}>
          {orgs.map((org) => (
            <OrgRow
              key={org.id}
              org={org}
              expanded={expandedId === org.id}
              onToggle={() => setExpandedId(expandedId === org.id ? null : org.id)}
            />
          ))}
        </div>
      )}

      <LoadMoreButton hasNextPage={query.hasNextPage} isFetchingNextPage={query.isFetchingNextPage} onClick={() => query.fetchNextPage()} />
    </section>
  );
}

function SummaryBar({ summary, isLoading }: { summary: AdminOrganizationsPage["summary"] | undefined; isLoading: boolean }) {
  if (isLoading) return <p className="muted">Loading summary…</p>;
  if (!summary) return null;
  return (
    <div className="card">
      <div className="stat-row">
        <Stat label="Organizations" value={summary.total.toLocaleString()} />
        <Stat label="Active" value={summary.active.toLocaleString()} />
        <Stat label="Suspended" value={summary.suspended.toLocaleString()} />
        <Stat label="With job errors (7d)" value={summary.orgs_with_job_errors_7d.toLocaleString()} />
      </div>
    </div>
  );
}

function OrgRow({ org, expanded, onToggle }: { org: AdminOrganization; expanded: boolean; onToggle: () => void }) {
  return (
    <div style={{ borderBottom: "1px solid var(--border)" }}>
      <div
        onClick={onToggle}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            if (e.key === " ") e.preventDefault();
            onToggle();
          }
        }}
        style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "0.65rem 1.1rem", cursor: "pointer", gap: "0.75rem", flexWrap: "wrap" }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          <strong style={{ fontSize: "0.92rem" }}>{org.name}</strong>
          <span className={`badge badge--${STATUS_ROLE[org.status]}`}>{org.status}</span>
          {org.is_operator && <span className="badge badge--neutral">operator</span>}
        </div>
        <div className="chip-row" style={{ fontSize: "0.8rem" }}>
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
      </div>
      {expanded && (
        <div style={{ padding: "0 1.1rem 1rem", background: "var(--plane)", borderTop: "1px solid var(--border)" }}>
          <OrgDetail org={org} />
        </div>
      )}
    </div>
  );
}

function OrgDetail({ org }: { org: AdminOrganization }) {
  const [tenantId, setTenantId] = useState(org.entra_tenant_id ?? "");
  const [mailbox, setMailbox] = useState(org.mailbox_connection?.mailbox_address ?? "");

  const updateOrg = useUpdateAdminOrganization(org.id);
  const setMailboxConnection = useSetAdminMailboxConnection(org.id);

  const connection = org.mailbox_connection;
  const consentRole = connection
    ? connection.consent_status === "granted"
      ? "good"
      : connection.consent_status === "revoked"
        ? "critical"
        : "warning"
    : "neutral";

  return (
    <div style={{ paddingTop: "0.75rem" }}>
      <div className="field-row">
        <span className="muted" style={{ fontSize: "0.78rem" }}>Status</span>
        <select
          className="input"
          value={org.status}
          onChange={(e) => updateOrg.mutate({ status: e.target.value as AdminOrganization["status"] })}
        >
          <option value="active">active</option>
          <option value="suspended">suspended</option>
        </select>
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
          client's own portal once they're in (see MailboxConnectionSection,
          rendered from Settings' GeneralTab.tsx and from Onboarding.tsx),
          so there's nothing left for the platform admin to relay
          out-of-band. */}

      {!org.entra_tenant_id && !org.is_operator && <CreateLocalUser orgId={org.id} />}

      <OrgUsersSection orgId={org.id} />

      <OperatorAccessSection org={org} />

      <DeleteOrgSection org={org} />
    </div>
  );
}

function OrgUsersSection({ orgId }: { orgId: string }) {
  const { data: users, isPending } = useAdminOrgUsers(orgId);
  const { resetPassword, resetMfa } = useAdminResetUser(orgId);
  const { copied, copy } = useClipboardFeedback();
  const [resetLink, setResetLink] = useState<{ email: string; link: string } | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const onError = (err: Error) => setError(err instanceof ApiError ? err.message : "reset failed");

  function handleResetPassword(user: AdminOrgUser) {
    if (!window.confirm(`Reset ${user.email}'s password? Their current password stops working immediately and they're signed out everywhere.`)) return;
    setError(null);
    setMessage(null);
    resetPassword.mutate(user.id, {
      onSuccess: (result) => setResetLink({ email: user.email, link: result.setup_link }),
      onError,
    });
  }

  function handleResetMfa(user: AdminOrgUser) {
    if (!window.confirm(`Reset ${user.email}'s two-factor authentication? They're signed out and must set up a new authenticator at their next sign-in.`)) return;
    setError(null);
    setResetLink(null);
    resetMfa.mutate(user.id, {
      onSuccess: () => setMessage(`${user.email} will set up a new authenticator the next time they sign in.`),
      onError,
    });
  }

  return (
    <div style={{ marginTop: "1rem", paddingTop: "0.75rem", borderTop: "1px solid var(--border)" }}>
      <div className="stat-tile-label" style={{ marginBottom: "0.3rem" }}>
        Users
      </div>
      {isPending && <p className="muted">Loading…</p>}
      {users && users.length === 0 && <p className="muted">No users yet.</p>}
      {users && users.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <tbody>
              {users.map((user) => (
                <tr key={user.id}>
                  <td>
                    {user.email}
                    <span className="muted"> · {user.role === "org_admin" ? "org admin" : "member"}</span>
                    {user.status === "disabled" && <span className="muted"> · disabled</span>}
                    {user.auth_method === "entra" && <span className="muted"> · Microsoft</span>}
                    {user.auth_method === "local" && !user.mfa_enrolled && <span className="muted"> · no MFA yet</span>}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    {user.auth_method === "local" && (
                      <div className="chip-row" style={{ justifyContent: "flex-end" }}>
                        <button className="btn btn--ghost btn--sm" onClick={() => handleResetPassword(user)} disabled={resetPassword.isPending}>
                          Reset password
                        </button>
                        {user.mfa_enrolled && (
                          <button className="btn btn--ghost btn--sm" onClick={() => handleResetMfa(user)} disabled={resetMfa.isPending}>
                            Reset MFA
                          </button>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {resetLink && (
        <>
          <p className="section-hint" style={{ marginTop: "0.6rem" }}>
            One-time setup link for {resetLink.email} — share it with them yourself. Their existing authenticator stays.
          </p>
          <div className="field-row">
            <code className="input" style={{ display: "inline-block" }}>
              {resetLink.link}
            </code>
            <button className="btn btn--secondary btn--sm" onClick={() => void copy(resetLink.link)}>
              {copied ? <Check /> : <Copy />}
              {copied ? "Copied" : "Copy link"}
            </button>
          </div>
        </>
      )}
      {message && (
        <div className="alert alert--good" style={{ marginTop: "0.6rem", marginBottom: 0 }}>
          {message}
        </div>
      )}
      {error && (
        <div className="alert alert--critical" style={{ marginTop: "0.6rem", marginBottom: 0 }}>
          {error}
        </div>
      )}
    </div>
  );
}

function OperatorAccessSection({ org }: { org: AdminOrganization }) {
  const [error, setError] = useState<string | null>(null);
  const updateOrg = useUpdateAdminOrganization(org.id);

  function toggle() {
    const message = org.is_operator
      ? `Remove admin console access from ${org.name}? Its org admins will no longer be able to manage the platform.`
      : `Give ${org.name}'s org admins access to this admin console? They'll be able to see and manage every organization.`;
    if (!window.confirm(message)) return;
    setError(null);
    updateOrg.mutate(
      { is_operator: !org.is_operator },
      { onError: (err) => setError(err instanceof ApiError ? err.message : "failed to update operator access") },
    );
  }

  return (
    <div style={{ marginTop: "1rem", paddingTop: "0.75rem", borderTop: "1px solid var(--border)" }}>
      <div className="stat-tile-label" style={{ marginBottom: "0.3rem" }}>
        Platform admin access
      </div>
      <p className="section-hint">
        {org.is_operator
          ? "This organization's org admins can use this admin console with their normal sign-in."
          : "Let this organization's org admins use this admin console with their normal sign-in."}
      </p>
      <button className="btn btn--secondary btn--sm" onClick={toggle} disabled={updateOrg.isPending}>
        {org.is_operator ? "Remove platform admin access" : "Make platform admin organization"}
      </button>
      {error && (
        <div className="alert alert--critical" style={{ marginTop: "0.6rem", marginBottom: 0 }}>
          {error}
        </div>
      )}
    </div>
  );
}

function CreateLocalUser({ orgId }: { orgId: string }) {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [setupLink, setSetupLink] = useState<string | null>(null);
  const { copied, copy: copyToClipboard } = useClipboardFeedback();

  const createUser = useCreateAdminUser(
    orgId,
    (created) => {
      setEmail("");
      setError(null);
      setSetupLink(created.setup_link);
    },
    (err) => setError(err instanceof ApiError ? err.message : "failed to create user"),
  );

  function copy() {
    if (!setupLink) return;
    void copyToClipboard(setupLink);
  }

  return (
    <div style={{ marginTop: "1rem", paddingTop: "0.75rem", borderTop: "1px solid var(--border)" }}>
      <div className="muted" style={{ fontSize: "0.78rem", marginBottom: "0.3rem" }}>
        No Entra tenant — bootstrap this org's first user
      </div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          createUser.mutate(email);
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
  const [confirming, setConfirming] = useState(false);
  const [confirmText, setConfirmText] = useState("");

  const deleteOrg = useDeleteAdminOrganization(org.id);

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

  const change = useChangeAdminPassword(
    () => {
      setError(null);
      setSuccess(true);
      setCurrentPassword("");
      setNewPassword("");
    },
    (err) => {
      setSuccess(false);
      setError(err instanceof ApiError ? err.message : "failed to change password");
    },
  );

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
        change.mutate({ current_password: currentPassword, new_password: newPassword });
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
