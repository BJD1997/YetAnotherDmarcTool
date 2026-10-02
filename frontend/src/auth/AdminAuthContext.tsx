import { createContext, useContext, type ReactNode } from "react";
import type { AdminMe } from "../api/types";
import { useCurrentAdmin } from "../hooks/useAuthResources";

interface AdminAuthContextValue {
  admin: AdminMe | null;
  // Both admin sign-ins are present and the console must ask which to use.
  needsChoice: boolean;
  isLoading: boolean;
  refetch: () => Promise<void>;
}

const AdminAuthContext = createContext<AdminAuthContextValue | undefined>(undefined);

export function AdminAuthProvider({ children }: { children: ReactNode }) {
  const { data, isLoading, refreshAdmin } = useCurrentAdmin();

  return (
    <AdminAuthContext.Provider
      value={{
        admin: data && data !== "choose" ? data : null,
        needsChoice: data === "choose",
        isLoading,
        // See AuthContext's refetch for why callers that navigate right
        // after this must await it.
        refetch: refreshAdmin,
      }}
    >
      {children}
    </AdminAuthContext.Provider>
  );
}

export function useAdminAuth(): AdminAuthContextValue {
  const ctx = useContext(AdminAuthContext);
  if (!ctx) throw new Error("useAdminAuth must be used within AdminAuthProvider");
  return ctx;
}
