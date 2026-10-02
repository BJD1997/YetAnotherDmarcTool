import { Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useAdminAuth } from "../auth/AdminAuthContext";
import AdminSessionChooser from "./AdminSessionChooser";

export default function RequireAdminAuth({ children }: { children: ReactNode }) {
  const { admin, needsChoice, isLoading } = useAdminAuth();

  if (isLoading) return <p className="muted" style={{ padding: "2rem" }}>Loading…</p>;
  if (needsChoice) return <AdminSessionChooser />;
  if (!admin) return <Navigate to="/admin/login" replace />;
  return <>{children}</>;
}
