import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { DetectedDomain } from "../api/types";
import { queryKeys } from "./queryKeys";

export function useDetectedDomains() {
  return useQuery({ queryKey: queryKeys.detectedDomains, queryFn: () => api.get<DetectedDomain[]>("/dmarc/detected-domains") });
}

export interface DetectedDomainAdded {
  added: string[];
  moved: string[];
  root: string;
}

// Adds the domain with its family: its detected root domain first, the
// root's other detected subdomains under it (see POST /domains/detected).
export function useAddDetectedDomain(onSuccess?: (result: DetectedDomainAdded) => void, onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (item: DetectedDomain) => api.post<DetectedDomainAdded>("/domains/detected", { name: item.name }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.domains.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.domains.ranked });
      queryClient.invalidateQueries({ queryKey: queryKeys.detectedDomains });
      queryClient.invalidateQueries({ queryKey: queryKeys.notifications });
      onSuccess?.(result);
    },
    onError,
  });
}

// A detected subdomain on its own, without its root domain: verified
// separately with its own TXT record. Adding the root later moves it under
// the root, keeping its verification.
export function useAddDetectedDomainAlone(onSuccess?: () => void, onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (item: DetectedDomain) =>
      api.post("/domains", { name: item.name, parent_domain_id: null, mail_profile: "sends_mail" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.domains.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.domains.ranked });
      queryClient.invalidateQueries({ queryKey: queryKeys.detectedDomains });
      queryClient.invalidateQueries({ queryKey: queryKeys.notifications });
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
      queryClient.invalidateQueries({ queryKey: queryKeys.notifications });
      onSuccess?.();
    },
    onError,
  });
}
