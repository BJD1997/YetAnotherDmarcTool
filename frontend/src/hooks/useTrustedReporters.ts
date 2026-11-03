import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import { queryKeys } from "./queryKeys";

export interface TrustedReporter {
  domain: string;
  created_at: string;
}

export function useTrustedReporters() {
  return useQuery({
    queryKey: queryKeys.organization.trustedReporters,
    queryFn: () => api.get<TrustedReporter[]>("/organizations/current/trusted-reporters"),
  });
}

// Trusting or removing a reporter re-evaluates reports already received, so
// every report view can change — same as changing the sender check itself.
export function useTrustReporter(onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (domain: string) => api.post<TrustedReporter[]>("/organizations/current/trusted-reporters", { domain }),
    onSuccess: () => void queryClient.invalidateQueries(),
    onError,
  });
}

export function useRemoveTrustedReporter(onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (domain: string) =>
      api.delete<TrustedReporter[]>(`/organizations/current/trusted-reporters/${encodeURIComponent(domain)}`),
    onSuccess: () => void queryClient.invalidateQueries(),
    onError,
  });
}
