import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";

import { api } from "../api/client";
import type { Domain, DomainMailProfile, VerifyDomainResponse } from "../api/types";
import type { RankedDomain } from "../api/overview";
import { queryKeys } from "./queryKeys";

type DomainsOptions = Pick<UseQueryOptions<Domain[]>, "enabled" | "refetchInterval">;

export function useDomains(options: DomainsOptions = {}) {
  return useQuery({
    queryKey: queryKeys.domains.all,
    queryFn: () => api.get<Domain[]>("/domains"),
    ...options,
  });
}

export function useDomain(domainId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.domains.detail(domainId ?? ""),
    queryFn: () => api.get<Domain>(`/domains/${domainId}`),
    enabled: !!domainId,
  });
}

export function useRankedDomains() {
  return useQuery({
    queryKey: queryKeys.domains.ranked,
    queryFn: () => api.get<RankedDomain[]>("/domains/ranked"),
  });
}

export function useVerifyDomain(domainId: string, onSuccess?: (result: VerifyDomainResponse) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<VerifyDomainResponse>(`/domains/${domainId}/verify`),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.domains.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.domains.ranked });
      queryClient.invalidateQueries({ queryKey: queryKeys.onboarding.status });
      onSuccess?.(result);
    },
  });
}

export function useDeleteDomain(domainId: string, onSuccess?: () => void, onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete(`/domains/${domainId}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.domains.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.domains.ranked });
      onSuccess?.();
    },
    onError,
  });
}

export function useUpdateDomain(
  domainId: string,
  onSuccess?: () => void,
  onError?: (error: Error) => void,
) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<Pick<Domain, "is_active" | "mail_profile">>) => api.patch<Domain>(`/domains/${domainId}`, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.domains.detail(domainId) });
      queryClient.invalidateQueries({ queryKey: queryKeys.domains.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.domains.ranked });
      queryClient.invalidateQueries({ queryKey: queryKeys.actionQueue(domainId) });
      onSuccess?.();
    },
    onError,
  });
}

export type { DomainMailProfile };

export function useCreateDomain(onSuccess?: (domain: Domain & { reattributed_reports: number; reattributed_records: number }) => void, onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; parent_domain_id: string | null; mail_profile: DomainMailProfile }) =>
      api.post<Domain & { reattributed_reports: number; reattributed_records: number }>("/domains", body),
    onSuccess: (domain) => {
      queryClient.invalidateQueries({ queryKey: queryKeys.domains.all });
      queryClient.invalidateQueries({ queryKey: queryKeys.domains.ranked });
      queryClient.invalidateQueries({ queryKey: queryKeys.onboarding.status });
      onSuccess?.(domain);
    },
    onError,
  });
}
