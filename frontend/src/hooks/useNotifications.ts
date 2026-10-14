import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api } from "../api/client";
import type { AppNotification } from "../api/notifications";
import { queryKeys } from "./queryKeys";

/** Open count for the bell's dot; polled so new ones show up without a reload. */
export function useNotificationsCount() {
  return useQuery({
    queryKey: queryKeys.notificationsCount,
    queryFn: () => api.get<{ open: number }>("/notifications/count"),
    refetchInterval: 60_000,
  });
}

export function useNotifications(enabled: boolean) {
  return useQuery({ queryKey: queryKeys.notifications, queryFn: () => api.get<AppNotification[]>("/notifications"), enabled });
}

export function useDismissNotification() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api.post<void>(`/notifications/${id}/dismiss`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: queryKeys.notifications }),
  });
}
