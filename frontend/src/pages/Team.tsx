import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";
import { api, ApiError } from "../api/client";
import type { TeamMember } from "../api/types";
import { useAuth } from "../auth/AuthContext";

export default function Team() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
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
      <Link to="/" className="back-link">
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
