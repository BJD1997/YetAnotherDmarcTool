import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";

import { api } from "../api/client";
import type { ImapSettingsIn, MailboxConnectionStatus } from "../api/dmarc";
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
    // ticket: from a test sign-in with Microsoft, for organizations that
    // don't sign in with Microsoft (it names the mailbox's tenant).
    mutationFn: (body: { mailbox_address: string; ticket?: string }) => api.put<MailboxConnectionStatus>("/mailbox-connection", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.mailboxConnection.current });
      // The mail-access consent links follow the mailbox's tenant.
      queryClient.invalidateQueries({ queryKey: queryKeys.organization.current });
      queryClient.invalidateQueries({ queryKey: queryKeys.onboarding.status });
      onSuccess?.();
    },
    onError,
  });
}

export function useTestImap() {
  return useMutation({
    mutationFn: (body: ImapSettingsIn) =>
      api.post<{ ok: true; messages: number; detected_authserv_id: string | null }>("/mailbox-connection/imap/test", body),
  });
}

export function useSetImapConnection(onSuccess?: () => void, onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ImapSettingsIn) => api.put<MailboxConnectionStatus>("/mailbox-connection/imap", body),
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

export function useStartMicrosoftMailboxTest() {
  return useMutation({
    mutationFn: () => api.post<{ start_url: string }>("/mailbox-connection/microsoft/test"),
  });
}
