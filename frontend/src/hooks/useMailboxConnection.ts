import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";

import { api } from "../api/client";
import type { MailboxConnectionStatus } from "../api/dmarc";
import type { MailboxJobRun } from "../api/dmarc";
import { queryKeys } from "./queryKeys";

type MailboxConnectionOptions = Pick<
  UseQueryOptions<MailboxConnectionStatus>,
  "enabled" | "refetchInterval" | "retry"
>;

export function useMailboxConnection({ retry = false, ...options }: MailboxConnectionOptions = {}) {
  return useQuery({
    queryKey: queryKeys.mailboxConnection.current,
    queryFn: () => api.get<MailboxConnectionStatus>("/mailbox-connection"),
    retry,
    ...options,
  });
}

export function useSetMailboxConnection(onSuccess?: () => void, onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (mailbox_address: string) => api.put<MailboxConnectionStatus>("/mailbox-connection", { mailbox_address }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.mailboxConnection.current });
      queryClient.invalidateQueries({ queryKey: queryKeys.onboarding.status });
      onSuccess?.();
    },
    onError,
  });
}

export function useResyncMailbox(onSuccess?: () => void) {
  return useMutation({ mutationFn: () => api.post("/mailbox-connection/resync"), onSuccess });
}

export function useMailboxJobRuns() {
  return useQuery({ queryKey: queryKeys.mailboxConnection.jobRuns, queryFn: () => api.get<MailboxJobRun[]>("/mailbox-connection/job-runs?limit=15") });
}
