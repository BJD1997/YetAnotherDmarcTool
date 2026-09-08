import { useQuery, type UseQueryOptions } from "@tanstack/react-query";

import { api } from "../api/client";
import type { Domain } from "../api/types";
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
