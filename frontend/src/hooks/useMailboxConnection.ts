import { useQuery, type UseQueryOptions } from "@tanstack/react-query";

import { api } from "../api/client";
import type { MailboxConnectionStatus } from "../api/dmarc";
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
