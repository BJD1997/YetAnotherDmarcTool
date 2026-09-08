import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { Domain } from "../api/types";
import type { ActionItem, Posture, SenderInventoryRow, SenderReviewUpdate, TrendPoint } from "../api/overview";
import { queryKeys } from "./queryKeys";

export function useHealth() {
  return useQuery({ queryKey: queryKeys.health, queryFn: () => api.get<{ version: string }>("/health"), staleTime: 5 * 60 * 1000 });
}

export function useTrend(domainId: string | null, days: number) {
  return useQuery({ queryKey: queryKeys.trend(domainId, days), queryFn: () => api.get<TrendPoint[]>(`/dmarc/trend?days=${days}${domainId ? `&domain_id=${domainId}` : ""}`) });
}

export function usePosture(domainId: string | null, days: number) {
  return useQuery({ queryKey: queryKeys.posture(domainId, days), queryFn: () => api.get<Posture>(`/dmarc/posture?days=${days}${domainId ? `&domain_id=${domainId}` : ""}`) });
}

export function useOverviewActionQueue(domainId: string | null) {
  return useQuery({ queryKey: queryKeys.actionQueue(domainId), queryFn: () => api.get<ActionItem[]>(`/action-queue${domainId ? `?domain_id=${domainId}` : ""}`) });
}

interface MergedSenderInventoryRow extends SenderInventoryRow { domain_id: string; domain_name: string }

export function useSenderInventory(domains: Domain[], days: number | null, sortRisk: (row: SenderInventoryRow) => number) {
  const ids = domains.map((domain) => domain.id).join(",");
  return useQuery({
    queryKey: queryKeys.senderInventory.list(ids, days),
    queryFn: async () => {
      const suffix = days === null ? "" : `?days=${days}`;
      const results = await Promise.all(domains.map(async (domain) => {
        const rows = await api.get<SenderInventoryRow[]>(`/domains/${domain.id}/dmarc/sender-inventory${suffix}`);
        return rows.map((row): MergedSenderInventoryRow => ({ ...row, domain_id: domain.id, domain_name: domain.name }));
      }));
      return results.flat().sort((a, b) => sortRisk(b) - sortRisk(a));
    },
    enabled: domains.length > 0,
  });
}

export function useUpdateSenderReview() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ domain_id, service_label, body }: { domain_id: string; service_label: string; body: Partial<Pick<SenderReviewUpdate, "status" | "owner">> }) =>
      api.patch<SenderReviewUpdate>(`/domains/${domain_id}/dmarc/sender-inventory/${encodeURIComponent(service_label)}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.senderInventory.all }),
  });
}
