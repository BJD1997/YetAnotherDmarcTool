import { useState } from "react";
import { Check, Copy, UserPlus } from "lucide-react";
import { ApiError } from "../api/client";
import type { TeamMember } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { useCurrentOrganization } from "../hooks/useOrganization";
import { useCreateUser, useRemoveUser, useResetUserMfa, useResetUserPassword, useUpdateUser, useUsers } from "../hooks/useUsers";
import { useClipboardFeedback } from "../hooks/useClipboardFeedback";
import { useSignInSettings } from "../hooks/useSignInSettings";

const PROVIDER_ACCOUNT = { microsoft: "Microsoft", google: "Google Workspace", oidc: "company" } as const;

function ShareSignInLink({ provider }: { provider: keyof typeof PROVIDER_ACCOUNT }) {
  const { copied, copy: copyToClipboard } = useClipboardFeedback();
  const { data: signIn } = useSignInSettings();
  const signInUrl = signIn?.login_link ?? `${window.location.origin}/login`;

  function copy() {
    void copyToClipboard(signInUrl);
  }

  return (
    <div className="card">
      <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
        Invite teammates
      </div>
      <p className="section-hint">
        There's no invite-by-email step: anyone who signs in with your organization's {PROVIDER_ACCOUNT[provider]} account
        at this link appears here automatically as a member.
      </p>
      <div className="field-row">
        <code className="input" style={{ display: "inline-block" }}>
          {signInUrl}
        </code>
        <button className="btn btn--secondary btn--sm" onClick={copy}>
          {copied ? <Check /> : <Copy />}
          {copied ? "Copied" : "Copy link"}
        </button>
      </div>
    </div>
  );
}

function AddLocalTeammate() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [setupLink, setSetupLink] = useState<string | null>(null);
  const { copied, copy: copyToClipboard } = useClipboardFeedback();

  const createUser = useCreateUser(
    (created) => {
      setEmail("");
      setError(null);
      setSetupLink(created.setup_link);
    },
    (err) => setError(err instanceof ApiError ? err.message : "failed to add teammate"),
  );

  function copy() {
    if (!setupLink) return;
    void copyToClipboard(setupLink);
  }

  return (
    <div className="card">
      <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
        Invite teammates
      </div>
      <p className="section-hint">
        Add a teammate's email and share the one-time setup link with them yourself; they set a password and an
        authenticator the first time they use it. These are local accounts. Once your organization signs in through
        Microsoft, Google or its own provider (Settings → Sign-in), people join that way instead, and only the
        break-glass account stays local.
      </p>
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
          placeholder="teammate@company.com"
          required
        />
        <button type="submit" className="btn btn--primary btn--sm" disabled={createUser.isPending}>
          <UserPlus />
          Generate setup link
        </button>
      </form>
      {error && (
        <div className="alert alert--critical" style={{ marginTop: "0.6rem", marginBottom: 0 }}>
          {error}
        </div>
      )}
      {setupLink && (
        <div className="field-row" style={{ marginTop: "0.75rem" }}>
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

function ResetLinkNotice({ email, link, onDismiss }: { email: string; link: string; onDismiss: () => void }) {
  const { copied, copy } = useClipboardFeedback();
  return (
    <div className="card">
      <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
        Password reset for {email}
      </div>
      <p className="section-hint">
        Their old password no longer works and they've been signed out everywhere. Share this one-time link with them
        yourself — they'll choose a new password and sign in with their existing authenticator.
      </p>
      <div className="field-row">
        <code className="input" style={{ display: "inline-block" }}>
          {link}
        </code>
        <button className="btn btn--secondary btn--sm" onClick={() => void copy(link)}>
          {copied ? <Check /> : <Copy />}
          {copied ? "Copied" : "Copy link"}
        </button>
        <button className="btn btn--ghost btn--sm" onClick={onDismiss}>
          Done
        </button>
      </div>
    </div>
  );
}

const PROVIDER_NAMES: Record<Exclude<TeamMember["auth_method"], "local">, string> = {
  entra: "Microsoft",
  google: "Google Workspace",
  oidc: "OpenID Connect",
};

export default function Team() {
  const { user } = useAuth();
  const { data: org } = useCurrentOrganization();
  const { data: members, isLoading } = useUsers();
  const [error, setError] = useState<string | null>(null);

  const canManage = user?.role === "org_admin";

  const [notice, setNotice] = useState<string | null>(null);
  const [resetLink, setResetLink] = useState<{ email: string; link: string } | null>(null);

  const onError = (fallback: string) => (err: Error) => setError(err instanceof ApiError ? err.message : fallback);
  const updateMember = useUpdateUser(() => setError(null), onError("failed to update user"));
  const resetPassword = useResetUserPassword(undefined, onError("failed to reset password"));
  const resetMfa = useResetUserMfa(undefined, onError("failed to reset two-factor authentication"));
  const removeMember = useRemoveUser(undefined, onError("failed to remove the account"));

  function handleRemove(member: TeamMember) {
    if (!window.confirm(`Remove this account of ${member.email}? Its sign-in history stays. This can't be undone.`)) return;
    setError(null);
    setNotice(null);
    setResetLink(null);
    removeMember.mutate(member.id, { onSuccess: () => setNotice(`Removed the account of ${member.email}.`) });
  }

  function handleResetPassword(member: TeamMember) {
    if (!window.confirm(`Reset ${member.email}'s password? Their current password stops working immediately and they're signed out everywhere.`)) return;
    setError(null);
    setNotice(null);
    resetPassword.mutate(member.id, { onSuccess: (result) => setResetLink({ email: member.email, link: result.setup_link }) });
  }

  function handleResetMfa(member: TeamMember) {
    if (!window.confirm(`Reset ${member.email}'s two-factor authentication? They're signed out and must set up a new authenticator at their next sign-in.`)) return;
    setError(null);
    setResetLink(null);
    resetMfa.mutate(member.id, {
      onSuccess: () => setNotice(`${member.email} will set up a new authenticator the next time they sign in.`),
    });
  }

  return (
    <section>
      <div className="page-header">
        <div>
          <h1>Team</h1>
          <p className="page-subtitle">
            New people start as members, except the admin named when the organization was set up. Promote them here
            if they need to manage domains, the mailbox connection, or other team members.
          </p>
        </div>
      </div>

      {org && org.sign_in_provider !== "none" && <ShareSignInLink provider={org.sign_in_provider} />}
      {/* Once the organization has a provider, people join through it; the
          break-glass account is then its only local account. */}
      {canManage && org?.sign_in_provider === "none" && <AddLocalTeammate />}

      {resetLink && <ResetLinkNotice {...resetLink} onDismiss={() => setResetLink(null)} />}
      {notice && <div className="alert alert--good">{notice}</div>}
      {error && <div className="alert alert--critical">{error}</div>}
      {isLoading && <p className="muted">Loading…</p>}

      <div className="card">
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Role</th>
                <th>Status</th>
                <th>Last login</th>
                {canManage && <th></th>}
              </tr>
            </thead>
            <tbody>
              {(members ?? []).map((member) => {
                const isSelf = member.id === user?.id;
                return (
                  <tr key={member.id}>
                    <td>
                      {member.email}
                      {isSelf && <span className="muted"> (you)</span>}
                    </td>
                    <td>
                      <span className={`badge ${member.role === "org_admin" ? "badge--good" : "badge--neutral"}`}>
                        {member.role === "org_admin" ? "org admin" : "member"}
                      </span>
                      {member.auth_method === "local" && (
                        <span className="badge badge--neutral" style={{ marginLeft: "0.3rem" }}>
                          {member.is_break_glass ? "break-glass" : "local"}
                        </span>
                      )}
                      {member.auth_method !== "local" && (
                        <span className="badge badge--neutral" style={{ marginLeft: "0.3rem" }}>
                          {PROVIDER_NAMES[member.auth_method]}
                        </span>
                      )}
                      {member.auth_method !== "local" && member.removable && (
                        <span
                          className="badge badge--warning"
                          style={{ marginLeft: "0.3rem" }}
                          title="Your organization no longer signs in through this provider, so nobody can sign in with this account."
                        >
                          previous provider
                        </span>
                      )}
                      {member.auth_method === "local" && !member.mfa_enrolled && (
                        <span className="badge badge--warning" style={{ marginLeft: "0.3rem" }}>
                          no MFA yet
                        </span>
                      )}
                    </td>
                    <td>
                      <span className={`badge ${member.status === "active" ? "badge--good" : "badge--neutral"}`}>{member.status}</span>
                    </td>
                    <td className="muted">{member.last_login_at ? new Date(member.last_login_at).toLocaleString() : "never"}</td>
                    {canManage && (
                      <td>
                        {!isSelf && (
                          <div className="chip-row">
                            {/* The break-glass account stays an active org admin. */}
                            {!member.is_break_glass && (
                              <>
                                <button
                                  className="btn btn--ghost btn--sm"
                                  onClick={() =>
                                    updateMember.mutate({
                                      id: member.id,
                                      body: { role: member.role === "org_admin" ? "member" : "org_admin" },
                                    })
                                  }
                                  disabled={updateMember.isPending}
                                >
                                  {member.role === "org_admin" ? "Demote to member" : "Promote to org admin"}
                                </button>
                                <button
                                  className="btn btn--ghost btn--sm"
                                  onClick={() =>
                                    updateMember.mutate({
                                      id: member.id,
                                      body: { status: member.status === "active" ? "disabled" : "active" },
                                    })
                                  }
                                  disabled={updateMember.isPending}
                                >
                                  {member.status === "active" ? "Disable" : "Re-enable"}
                                </button>
                              </>
                            )}
                            {member.auth_method === "local" && (
                              <>
                                <button
                                  className="btn btn--ghost btn--sm"
                                  onClick={() => handleResetPassword(member)}
                                  disabled={resetPassword.isPending}
                                >
                                  Reset password
                                </button>
                                {member.mfa_enrolled && (
                                  <button
                                    className="btn btn--ghost btn--sm"
                                    onClick={() => handleResetMfa(member)}
                                    disabled={resetMfa.isPending}
                                  >
                                    Reset MFA
                                  </button>
                                )}
                              </>
                            )}
                            {member.removable && (
                              <button className="btn btn--ghost btn--sm" onClick={() => handleRemove(member)} disabled={removeMember.isPending}>
                                Remove
                              </button>
                            )}
                          </div>
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}
