import { Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useAdminAuth } from "../auth/AdminAuthContext";

export default function RequireAdminAuth({ children }: { children: ReactNode }) {
  const { admin, isLoading } = useAdminAuth();

  if (isLoading) return <p className="muted" style={{ padding: "2rem" }}>Loading…</p>;
  if (!admin) return <Navigate to="/admin/login" replace />;
  return <>{children}</>;
}
