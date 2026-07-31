import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Organization } from "../api/types";
import { useAuth } from "../auth/AuthContext";

export default function Shell({ children }: { children: ReactNode }) {
  const { user, refetch } = useAuth();
  const queryClient = useQueryClient();
  const { data: org } = useQuery({
    queryKey: ["organization", "current"],
    queryFn: () => api.get<Organization>("/organizations/current"),
    enabled: !!user,
  });

  async function handleLogout() {
    await api.post("/auth/logout");
    queryClient.clear();
    refetch();
  }

  return (
    <div style={{ fontFamily: "system-ui, sans-serif" }}>
      <header
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "0.75rem 1.5rem",
          borderBottom: "1px solid #e5e7eb",
        }}
      >
        <div>
          <Link to="/" style={{ color: "inherit", textDecoration: "none" }}>
            <strong>DMARC Dashboard{org ? ` — ${org.name}` : ""}</strong>
          </Link>
          <Link to="/team" style={{ marginLeft: "1.5rem" }}>
            Team
          </Link>
          {org?.is_operator && user?.role === "org_admin" && (
            <Link to="/admin" style={{ marginLeft: "1.5rem" }}>
              Manage organizations
            </Link>
          )}
        </div>
        <div>
          <span style={{ marginRight: "1rem", color: "#4b5563" }}>{user?.email}</span>
          <button onClick={handleLogout}>Sign out</button>
        </div>
      </header>
      <main style={{ padding: "1.5rem" }}>{children}</main>
    </div>
  );
}
