import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { MailboxConnectionStatus } from "../api/dmarc";
import { queryKeys } from "./queryKeys";

export function useMailboxConnection({ retry = false }: { retry?: boolean } = {}) {
  return useQuery({
    queryKey: queryKeys.mailboxConnection.current,
    queryFn: () => api.get<MailboxConnectionStatus>("/mailbox-connection"),
    retry,
  });
}
