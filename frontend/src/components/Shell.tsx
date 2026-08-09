import { useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LayoutDashboard, Globe, Users, Building2, LogOut, Menu, X, Settings } from "lucide-react";
import { api } from "../api/client";
import type { Organization } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import ThemeToggle from "./ThemeToggle";

export default function Shell({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [mobileOpen, setMobileOpen] = useState(false);
  const { data: org } = useQuery({
    queryKey: ["organization", "current"],
    queryFn: () => api.get<Organization>("/organizations/current"),
    enabled: !!user,
  });

  // Same population that can see /admin — a plain org user would just get
  // a 401 from admin-only routes, so links into that area are gated on the
  // same condition rather than being shown and failing.
  const canSeeAdmin = !!(org?.is_operator && user?.role === "org_admin");

  // Shown to every user, not just admins — /api/health needs no auth.
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: () => api.get<{ version: string }>("/health"),
    staleTime: 5 * 60 * 1000,
  });

  async function handleLogout() {
    try {
      await api.post("/auth/logout");
    } finally {
      // A hard navigation (not react-query cache invalidation) so this is
      // deterministic regardless of query-observer timing — logout should
      // never require a second click to actually leave the app.
      queryClient.clear();
      window.location.href = "/login";
    }
  }

  const isActive = (path: string) => (path === "/" ? location.pathname === "/" : location.pathname.startsWith(path));
  const closeMobile = () => setMobileOpen(false);

  return (
    <div className="app-shell">
      <div className="mobile-topbar">
        <button className="icon-btn" onClick={() => setMobileOpen(true)} aria-label="Open menu">
          <Menu />
        </button>
        <span className="sidebar-brand-mark">Y</span>
        <span className="sidebar-brand-text">YetAnotherDmarcTool</span>
      </div>

      <div className={`sidebar-backdrop ${mobileOpen ? "is-open" : ""}`} onClick={closeMobile} />

      <aside className={`sidebar ${mobileOpen ? "is-open" : ""}`}>
        <div className="sidebar-brand" style={{ justifyContent: "space-between" }}>
          <Link to="/" onClick={closeMobile} style={{ display: "flex", alignItems: "center", gap: "0.6rem", color: "inherit" }}>
            <span className="sidebar-brand-mark">Y</span>
            <span className="sidebar-brand-text">YetAnotherDmarcTool</span>
          </Link>
          <button className="icon-btn" onClick={closeMobile} aria-label="Close menu" style={{ display: mobileOpen ? "inline-flex" : "none" }}>
            <X />
          </button>
        </div>
        {org && <div className="sidebar-org">{org.name}</div>}

        <nav className="sidebar-nav">
          <Link to="/" onClick={closeMobile} className={`nav-link ${isActive("/") ? "active" : ""}`}>
            <LayoutDashboard />
            Overview
          </Link>
          <Link to="/domains" onClick={closeMobile} className={`nav-link ${isActive("/domains") ? "active" : ""}`}>
            <Globe />
            Domains
          </Link>
          <Link to="/team" onClick={closeMobile} className={`nav-link ${isActive("/team") ? "active" : ""}`}>
            <Users />
            Team
          </Link>
          <Link to="/settings" onClick={closeMobile} className={`nav-link ${isActive("/settings") ? "active" : ""}`}>
            <Settings />
            Settings
          </Link>
          {canSeeAdmin && (
            <Link to="/admin" onClick={closeMobile} className={`nav-link ${isActive("/admin") ? "active" : ""}`}>
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
          <div className="sidebar-legal" style={{ display: "flex", justifyContent: "space-between", gap: "0.5rem" }}>
            <span title="Running version">{health?.version ?? "…"}</span>
            <a href="https://github.com/BJD1997/YetAnotherDmarcTool" target="_blank" rel="noopener noreferrer">
              GitHub
            </a>
          </div>
        </div>
      </aside>
      <main className="app-main">
        <div className="app-main-inner">{children}</div>
      </main>
    </div>
  );
}
