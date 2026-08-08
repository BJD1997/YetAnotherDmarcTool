import { createContext, useContext, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { AdminMe } from "../api/types";

interface AdminAuthContextValue {
  admin: AdminMe | null;
  isLoading: boolean;
  refetch: () => Promise<void>;
}

const AdminAuthContext = createContext<AdminAuthContextValue | undefined>(undefined);

export function AdminAuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["admin-me"],
    queryFn: async () => {
      try {
        return await api.get<AdminMe>("/admin/me");
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      }
    },
    retry: false,
  });

  return (
    <AdminAuthContext.Provider
      value={{
        admin: data ?? null,
        isLoading,
        // See AuthContext's refetch for why callers that navigate right
        // after this must await it.
        refetch: () => queryClient.invalidateQueries({ queryKey: ["admin-me"] }),
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
