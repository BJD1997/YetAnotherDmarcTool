import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { CheckResult, DetectedSelector, DkimSelectorItem } from "../api/dnsChecks";
import { queryKeys } from "./queryKeys";

export function useDnsChecks(domainId: string) {
  return useQuery({ queryKey: queryKeys.dnsChecks(domainId), queryFn: () => api.get<CheckResult[]>(`/domains/${domainId}/checks`) });
}

export function useRecheckDns(domainId: string, onSuccess?: (checks: CheckResult[]) => void, onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<CheckResult[]>(`/domains/${domainId}/checks/recheck`),
    onSuccess: (checks) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.dnsChecks(domainId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.onboarding.status });
      onSuccess?.(checks);
    },
    onError,
  });
}

export function useDkimSelectors(domainId: string) {
  return useQuery({ queryKey: queryKeys.dkimSelectors(domainId), queryFn: () => api.get<DkimSelectorItem[]>(`/domains/${domainId}/selectors`) });
}

export function useDetectedDkimSelectors(domainId: string) {
  return useQuery({ queryKey: queryKeys.detectedDkimSelectors(domainId), queryFn: () => api.get<DetectedSelector[]>(`/domains/${domainId}/selectors/detected`) });
}

export function useAddDkimSelector(domainId: string, onSuccess?: () => void, onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (selector: string) => api.post<DkimSelectorItem>(`/domains/${domainId}/selectors`, { selector }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.dkimSelectors(domainId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.detectedDkimSelectors(domainId) });
      onSuccess?.();
    },
    onError,
  });
}

export function useDeleteDkimSelector(domainId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.delete(`/domains/${domainId}/selectors/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.dkimSelectors(domainId) }),
  });
}
