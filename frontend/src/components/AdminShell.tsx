import { useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Building2, ListChecks, LogOut, Menu, X, ArrowLeft } from "lucide-react";
import { api } from "../api/client";
import { useAdminAuth } from "../auth/AdminAuthContext";
import ThemeToggle from "./ThemeToggle";

export default function AdminShell({ children }: { children: ReactNode }) {
  const { admin, refetch } = useAdminAuth();
  const location = useLocation();
  const queryClient = useQueryClient();
  const [mobileOpen, setMobileOpen] = useState(false);

  async function handleLogout() {
    // Only a "local" session has anything for /admin/logout to revoke — an
    // operator_org admin is really just using their normal dashboard login,
    // signing out of that belongs in the main Shell instead.
    if (admin?.auth_type === "local") {
      await api.post("/admin/logout");
      queryClient.clear();
      refetch();
    }
  }

  const isActive = (path: string) => (path === "/admin" ? location.pathname === "/admin" : location.pathname.startsWith(path));
  const closeMobile = () => setMobileOpen(false);

  return (
    <div className="app-shell">
      <div className="mobile-topbar">
        <button className="icon-btn" onClick={() => setMobileOpen(true)} aria-label="Open menu">
          <Menu />
        </button>
        <span className="sidebar-brand-mark">D</span>
        <span className="sidebar-brand-text">DMARCwatch admin</span>
      </div>

      <div className={`sidebar-backdrop ${mobileOpen ? "is-open" : ""}`} onClick={closeMobile} />

      <aside className={`sidebar ${mobileOpen ? "is-open" : ""}`}>
        <div className="sidebar-brand" style={{ justifyContent: "space-between" }}>
          <Link to="/admin" onClick={closeMobile} style={{ display: "flex", alignItems: "center", gap: "0.6rem", color: "inherit" }}>
            <span className="sidebar-brand-mark">D</span>
            <span className="sidebar-brand-text">DMARCwatch</span>
          </Link>
          <button className="icon-btn" onClick={closeMobile} aria-label="Close menu" style={{ display: mobileOpen ? "inline-flex" : "none" }}>
            <X />
          </button>
        </div>
        <div className="sidebar-org">Platform admin</div>

        <nav className="sidebar-nav">
          <Link to="/admin" onClick={closeMobile} className={`nav-link ${isActive("/admin") ? "active" : ""}`}>
            <Building2 />
            Organizations
          </Link>
          <Link to="/admin/job-runs" onClick={closeMobile} className={`nav-link ${isActive("/admin/job-runs") ? "active" : ""}`}>
            <ListChecks />
            Job runs
          </Link>
          {admin?.auth_type === "operator_org" && (
            <Link to="/" onClick={closeMobile} className="nav-link">
              <ArrowLeft />
              Back to dashboard
            </Link>
          )}
        </nav>

        <div className="sidebar-footer">
          <div className="sidebar-user">
            <span className="sidebar-user-email" title={admin?.email}>
              {admin?.email}
            </span>
            <div style={{ display: "flex", gap: "0.25rem", flexShrink: 0 }}>
              <ThemeToggle />
              {admin?.auth_type === "local" && (
                <button className="icon-btn" onClick={handleLogout} title="Sign out" aria-label="Sign out">
                  <LogOut />
                </button>
              )}
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
