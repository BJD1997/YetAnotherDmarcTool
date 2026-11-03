import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { queryKeys } from "./queryKeys";

export interface LeftOutReport {
  id: string;
  type: "DMARC" | "TLS-RPT" | "Forensic";
  reporter: string | null;
  period_start: string;
  received_at: string;
  sender_domain: string | null;
  reason: string;
  // Whether trusting sender_domain is all it would take to count it.
  trustable: boolean;
}

export function useLeftOutReports(domainId: string) {
  return useQuery({
    queryKey: queryKeys.domains.leftOutReports(domainId),
    queryFn: () => api.get<LeftOutReport[]>(`/domains/${domainId}/left-out-reports`),
  });
}
