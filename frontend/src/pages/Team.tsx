import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, Copy, UserPlus } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { Organization, TeamMember } from "../api/types";
import { useAuth } from "../auth/AuthContext";

function ShareSignInLink() {
  const [copied, setCopied] = useState(false);
  const signInUrl = `${window.location.origin}/login`;

  function copy() {
    navigator.clipboard.writeText(signInUrl).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="card">
      <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
        Invite teammates
      </div>
      <p className="section-hint">
        There's no invite-by-email step — anyone in your Microsoft tenant who signs in at this link will appear here
        automatically as a member.
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
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [setupLink, setSetupLink] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const createUser = useMutation({
    mutationFn: () => api.post<TeamMember & { setup_link: string }>("/users", { email }),
    onSuccess: (created) => {
      setEmail("");
      setError(null);
      setSetupLink(created.setup_link);
      queryClient.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "failed to add teammate"),
  });

  function copy() {
    if (!setupLink) return;
    navigator.clipboard.writeText(setupLink).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="card">
      <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
        Invite teammates
      </div>
      <p className="section-hint">
        There's no Microsoft SSO for this organization — add a teammate's email below and share the one-time setup
        link it generates with them yourself. They'll set a password and enroll two-factor authentication the first
        time they use it.
      </p>
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

export default function Team() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const { data: org } = useQuery({
    queryKey: ["organization", "current"],
    queryFn: () => api.get<Organization>("/organizations/current"),
  });
  const { data: members, isLoading } = useQuery({
    queryKey: ["users"],
    queryFn: () => api.get<TeamMember[]>("/users"),
  });
  const [error, setError] = useState<string | null>(null);

  const canManage = user?.role === "org_admin";

  const updateMember = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<Pick<TeamMember, "role" | "status">> }) =>
      api.patch<TeamMember>(`/users/${id}`, body),
    onSuccess: () => {
      setError(null);
      queryClient.invalidateQueries({ queryKey: ["users"] });
    },
    onError: (err) => setError(err instanceof ApiError ? err.message : "failed to update user"),
  });

  return (
    <section>
      <Link to="/domains" className="back-link">
        <ArrowLeft size={14} />
        Domains
      </Link>
      <div className="page-header">
        <div>
          <h1>Team</h1>
          <p className="page-subtitle">
            The first person to sign in to a new organization automatically becomes an org admin. Everyone after
            that starts as a member — promote them here if they need to manage domains, the mailbox connection, or
            other team members.
          </p>
        </div>
      </div>

      {org && (org.entra_tenant_id ? <ShareSignInLink /> : canManage ? <AddLocalTeammate /> : null)}

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
                          local
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
