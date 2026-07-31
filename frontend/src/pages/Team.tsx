import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
      <p>
        <a href="/">&larr; Domains</a>
      </p>
      <h2>Team</h2>
      <p style={{ color: "#4b5563", maxWidth: 640 }}>
        The first person to sign in to a new organization automatically becomes an org admin.
        Everyone after that starts as a member — promote them here if they need to manage domains,
        the mailbox connection, or other team members.
      </p>

      {error && <p style={{ color: "#b91c1c" }}>{error}</p>}
      {isLoading && <p>Loading…</p>}

      <table style={{ borderCollapse: "collapse", marginTop: "1rem" }}>
        <thead>
          <tr style={{ textAlign: "left" }}>
            <th style={{ paddingRight: "1.5rem" }}>Email</th>
            <th style={{ paddingRight: "1.5rem" }}>Role</th>
            <th style={{ paddingRight: "1.5rem" }}>Status</th>
            <th style={{ paddingRight: "1.5rem" }}>Last login</th>
            {canManage && <th></th>}
          </tr>
        </thead>
        <tbody>
          {(members ?? []).map((member) => {
            const isSelf = member.id === user?.id;
            return (
              <tr key={member.id} style={{ borderTop: "1px solid #f3f4f6" }}>
                <td style={{ paddingRight: "1.5rem" }}>
                  {member.email}
                  {isSelf && " (you)"}
                </td>
                <td style={{ paddingRight: "1.5rem" }}>{member.role}</td>
                <td style={{ paddingRight: "1.5rem" }}>{member.status}</td>
                <td style={{ paddingRight: "1.5rem" }}>
                  {member.last_login_at ? new Date(member.last_login_at).toLocaleString() : "never"}
                </td>
                {canManage && (
                  <td>
                    {!isSelf && (
                      <>
                        <button
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
                          style={{ marginLeft: "0.5rem" }}
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
                  </td>
                )}
              </tr>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}
