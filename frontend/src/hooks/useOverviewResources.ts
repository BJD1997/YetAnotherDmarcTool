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

export function useSenderInventory(
  domains: Domain[],
  days: number | null,
  sortRisk: (row: SenderInventoryRow) => number,
  ready = true,
) {
  const ids = domains.map((domain) => domain.id).join(",");
  return useQuery({
    queryKey: queryKeys.senderInventory.list(ids, days),
    queryFn: async () => {
      // One batched call instead of one per domain: N concurrent per-domain
      // requests each held a DB connection for the duration of the backend's
      // DNS-bound sender-identification work, which is the same shape of
      // load that forced the pool size up (see config.py's db_pool_size
      // comment) — see sender_inventory_multi's docstring on the backend.
      const daysParam = days === null ? "" : `&days=${days}`;
      const domainIdsParam = domains.map((domain) => `domain_ids=${domain.id}`).join("&");
      const byDomain = await api.get<Record<string, SenderInventoryRow[]>>(`/dmarc/sender-inventory?${domainIdsParam}${daysParam}`);
      const domainNames = new Map(domains.map((domain) => [domain.id, domain.name]));
      const results: MergedSenderInventoryRow[] = Object.entries(byDomain).flatMap(([domainId, rows]) =>
        rows.map((row) => ({ ...row, domain_id: domainId, domain_name: domainNames.get(domainId) ?? "" }))
      );
      return results.sort((a, b) => sortRisk(b) - sortRisk(a));
    },
    // `ready` gates this behind the Overview page's viewport lazy-load (see
    // SenderInventory.tsx's useInView) — this is the heaviest of the page's
    // parallel queries (DNS-bound identify_many), so keeping it out of the
    // initial load burst until the card is actually scrolled to matters more
    // here than for the page's other, cheaper queries.
    enabled: domains.length > 0 && ready,
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
