import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { DetectedDomain, Domain } from "../api/types";
import { queryKeys } from "./queryKeys";

export function useDetectedDomains() {
  return useQuery({ queryKey: queryKeys.detectedDomains, queryFn: () => api.get<DetectedDomain[]>("/dmarc/detected-domains") });
}

export function useAddDetectedDomain(onSuccess?: () => void, onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (item: DetectedDomain) => api.post<Domain & { reattributed_reports: number; reattributed_records: number }>("/domains", { name: item.name, parent_domain_id: item.suggested_parent_id }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.domains.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.domains.ranked });
      queryClient.invalidateQueries({ queryKey: queryKeys.detectedDomains });
      onSuccess?.();
    },
    onError,
  });
}

export function useDismissDetectedDomain(onSuccess?: () => void, onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (item: DetectedDomain) => api.post<void>(`/dmarc/detected-domains/${encodeURIComponent(item.name)}/dismiss`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.detectedDomains });
      onSuccess?.();
    },
    onError,
  });
}
