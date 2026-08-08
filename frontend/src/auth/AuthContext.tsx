import { createContext, useContext, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";
import type { CurrentUser } from "../api/types";

interface AuthContextValue {
  user: CurrentUser | null;
  isLoading: boolean;
  refetch: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api.get<CurrentUser>("/auth/me");
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      }
    },
    retry: false,
  });

  return (
    <AuthContext.Provider
      value={{
        user: data ?? null,
        isLoading,
        // Callers that navigate right after this (Login/SetPassword) need
        // to await it — invalidateQueries' promise resolves once the
        // refetch itself completes, not just once it's kicked off, so
        // RequireAuth sees the real post-login user instead of racing
        // ahead on stale (pre-login, cached-null) data. That race was the
        // exact cause of a reported bug: local-auth sign-in bouncing back
        // to the login form once before landing on the dashboard the
        // second time.
        refetch: () => queryClient.invalidateQueries({ queryKey: ["me"] }),
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}
