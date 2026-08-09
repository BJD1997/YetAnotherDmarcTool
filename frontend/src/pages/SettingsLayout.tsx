import { NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";
import type { Organization } from "../api/types";
import { useAuth } from "../auth/AuthContext";

export default function SettingsLayout() {
  const { user } = useAuth();
  const canManage = user?.role === "org_admin";
  const { data: org } = useQuery({
    queryKey: ["organization", "current"],
    queryFn: () => api.get<Organization>("/organizations/current"),
  });

  // Domains/sign-in activity mutate org-wide state and are admin-only,
  // same bar the flat Settings page used to apply per-section — hidden
  // from the tab nav entirely for a plain member, not just the content.
  const tabs: { to: string; label: string; end?: boolean }[] = [
    { to: "", label: "General", end: true },
    ...(canManage
      ? [
          { to: "domains", label: "Domains" },
          { to: "sign-in-activity", label: "Sign-in activity" },
        ]
      : []),
  ];

  return (
    <section>
      <div className="page-header">
        <h1>Settings</h1>
      </div>
      <nav className="detail-tabs">
        {tabs.map((tab) => (
          <NavLink key={tab.to} to={tab.to || "."} end={tab.end} className={({ isActive }) => `detail-tab ${isActive ? "active" : ""}`}>
            {tab.label}
          </NavLink>
        ))}
      </nav>
      {org ? <Outlet context={org} /> : <p className="muted">Loading…</p>}
    </section>
  );
}
