import { Navigate } from "react-router-dom";
import type { ReactNode } from "react";
import { useAuth } from "../auth/AuthContext";

export default function RequireAuth({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();

  if (isLoading) return <p className="muted" style={{ padding: "2rem" }}>Loading…</p>;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}
