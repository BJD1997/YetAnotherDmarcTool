import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "../api/client";
import type { AdminMe, CurrentUser } from "../api/types";
import { queryKeys } from "./queryKeys";

export function useCurrentUser() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.currentUser,
    queryFn: async () => {
      try { return await api.get<CurrentUser>("/auth/me"); }
      catch (error) { if (error instanceof ApiError && error.status === 401) return null; throw error; }
    },
    retry: false,
  });
  return { ...query, refreshUser: () => queryClient.invalidateQueries({ queryKey: queryKeys.currentUser }) };
}

export function useCurrentAdmin() {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.admin.currentUser,
    queryFn: async () => {
      try { return await api.get<AdminMe>("/admin/me"); }
      catch (error) { if (error instanceof ApiError && error.status === 401) return null; throw error; }
    },
    retry: false,
  });
  return { ...query, refreshAdmin: () => queryClient.invalidateQueries({ queryKey: queryKeys.admin.currentUser }) };
}
