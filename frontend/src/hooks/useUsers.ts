import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { TeamMember } from "../api/types";
import { queryKeys } from "./queryKeys";

export function useUsers() {
  return useQuery({
    queryKey: queryKeys.users.all,
    queryFn: () => api.get<TeamMember[]>("/users"),
  });
}

export function useCreateUser(onSuccess?: (user: TeamMember & { setup_link: string }) => void, onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (email: string) => api.post<TeamMember & { setup_link: string }>("/users", { email }),
    onSuccess: (user) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.users.all });
      onSuccess?.(user);
    },
    onError,
  });
}

export function useUpdateUser(onSuccess?: () => void, onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: Partial<Pick<TeamMember, "role" | "status">> }) => api.patch<TeamMember>(`/users/${id}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.users.all });
      onSuccess?.();
    },
    onError,
  });
}
