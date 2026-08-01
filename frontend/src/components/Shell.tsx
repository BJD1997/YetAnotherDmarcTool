import type { ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe, Users, Building2, LogOut } from "lucide-react";
import { api } from "../api/client";
import type { Organization } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import ThemeToggle from "./ThemeToggle";

export default function Shell({ children }: { children: ReactNode }) {
  const { user, refetch } = useAuth();
  const location = useLocation();
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

  const isActive = (path: string) => (path === "/" ? location.pathname === "/" : location.pathname.startsWith(path));

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Link to="/" className="sidebar-brand">
          <span className="sidebar-brand-mark">D</span>
          <span className="sidebar-brand-text">DMARCwatch</span>
        </Link>
        {org && <div className="sidebar-org">{org.name}</div>}

        <nav className="sidebar-nav">
          <Link to="/" className={`nav-link ${isActive("/") ? "active" : ""}`}>
            <Globe />
            Domains
          </Link>
          <Link to="/team" className={`nav-link ${isActive("/team") ? "active" : ""}`}>
            <Users />
            Team
          </Link>
          {org?.is_operator && user?.role === "org_admin" && (
            <Link to="/admin" className={`nav-link ${isActive("/admin") ? "active" : ""}`}>
              <Building2 />
              Manage organizations
            </Link>
          )}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-user">
            <span className="sidebar-user-email" title={user?.email}>
              {user?.email}
            </span>
            <div style={{ display: "flex", gap: "0.25rem", flexShrink: 0 }}>
              <ThemeToggle />
              <button className="icon-btn" onClick={handleLogout} title="Sign out" aria-label="Sign out">
                <LogOut />
              </button>
            </div>
          </div>
        </div>
      </aside>
      <main className="app-main">
        <div className="app-main-inner">{children}</div>
      </main>
    </div>
  );
}
