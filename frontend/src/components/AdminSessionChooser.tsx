import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { AdminSessionOptions } from "../api/types";
import { useAdminAuth } from "../auth/AdminAuthContext";

// Shown when this browser is signed in both to the break-glass admin account
// and to an organization with platform admin access (the two can even share
// an email address) — the console asks instead of guessing which to act as.
export default function AdminSessionChooser() {
  const { refetch } = useAdminAuth();
  const [error, setError] = useState<string | null>(null);
  const [choosing, setChoosing] = useState(false);
  const { data: options } = useQuery({
    queryKey: ["admin-session-options"],
    queryFn: () => api.get<AdminSessionOptions>("/admin/session-options"),
  });

  async function choose(choice: "local" | "operator_org") {
    setChoosing(true);
    setError(null);
    try {
      await api.post("/admin/session-choice", { choice });
      await refetch();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "couldn't switch sign-in");
      setChoosing(false);
    }
  }

  return (
    <main className="auth-page">
      <div className="auth-card">
        <div className="auth-brand">
          <span className="sidebar-brand-mark">Y</span>
          <span className="sidebar-brand-text" style={{ fontSize: "1.1rem" }}>
            YetAnotherDmarcTool admin
          </span>
        </div>
        <p className="page-subtitle" style={{ marginBottom: "1rem" }}>
          You're signed in two ways. Which one should the admin console use?
        </p>
        {!options && <p className="muted">Loading…</p>}
        {options && (
          <div className="auth-form">
            {options.operator_org && (
              <button className="btn btn--primary" disabled={choosing} onClick={() => choose("operator_org")} style={{ padding: "0.65rem", flexDirection: "column", alignItems: "flex-start" }}>
                <span>Your organization's sign-in</span>
                <span style={{ fontSize: "0.8rem", fontWeight: 400, opacity: 0.85 }}>
                  {options.operator_org.email} · {options.operator_org.organization_name}
                </span>
              </button>
            )}
            {options.local && (
              <button className="btn btn--secondary" disabled={choosing} onClick={() => choose("local")} style={{ padding: "0.65rem", flexDirection: "column", alignItems: "flex-start" }}>
                <span>Break-glass admin login</span>
                <span style={{ fontSize: "0.8rem", fontWeight: 400, opacity: 0.85 }}>{options.local.email}</span>
              </button>
            )}
            {error && (
              <div className="alert alert--critical" style={{ margin: 0 }}>
                {error}
              </div>
            )}
            <p className="section-hint" style={{ margin: 0 }}>
              You can switch later from the admin sidebar. Signing out of the break-glass login also ends this question.
            </p>
          </div>
        )}
      </div>
    </main>
  );
}
