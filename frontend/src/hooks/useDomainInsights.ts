import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { DmarcSummary, DomainRating, InboundHostRow } from "../api/dmarc";
import type { ActionItem } from "../api/overview";
import { queryKeys } from "./queryKeys";

export function useDmarcSummary(domainId: string) {
  return useQuery({ queryKey: queryKeys.dmarcSummary(domainId), queryFn: () => api.get<DmarcSummary>(`/domains/${domainId}/dmarc/summary`) });
}

export function useDomainRating(domainId: string) {
  return useQuery({ queryKey: queryKeys.domainRating(domainId), queryFn: () => api.get<DomainRating>(`/domains/${domainId}/rating`) });
}

export function useActionQueue(domainId: string) {
  return useQuery({ queryKey: queryKeys.actionQueue(domainId), queryFn: () => api.get<ActionItem[]>(`/action-queue?domain_id=${domainId}`) });
}

export function useInboundHosts(domainId: string, enabled = true) {
  return useQuery({ queryKey: queryKeys.inboundHosts(domainId), queryFn: () => api.get<InboundHostRow[]>(`/domains/${domainId}/dmarc/inbound`), enabled });
}

export function useRuaCheck(domainId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.ruaCheck(domainId ?? ""),
    queryFn: () => api.get<{ status: string; org_mailbox_address: string | null }>(`/domains/${domainId}/dmarc/rua-check`),
    enabled: !!domainId,
  });
}
