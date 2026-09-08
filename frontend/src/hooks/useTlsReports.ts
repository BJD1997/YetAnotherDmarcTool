import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { TlsRptReportRow, TlsRptSenderSummary, TlsRptSummary } from "../api/dmarc";
import { queryKeys } from "./queryKeys";

export function useTlsReportSummary(domainId: string, filters: string) {
  return useQuery({ queryKey: queryKeys.tlsReports.summary(domainId, filters), queryFn: () => api.get<TlsRptSummary>(`/domains/${domainId}/dmarc/tls-rpt/summary${filters ? `?${filters}` : ""}`) });
}

export function useTlsReportRows(domainId: string, filters: string, enabled: boolean) {
  return useQuery({ queryKey: queryKeys.tlsReports.reports(domainId, filters), queryFn: () => api.get<TlsRptReportRow[]>(`/domains/${domainId}/dmarc/tls-rpt/reports${filters ? `?${filters}` : ""}`), enabled });
}

export function useTlsReportsBySender(domainId: string, filters: string, enabled: boolean) {
  return useQuery({ queryKey: queryKeys.tlsReports.bySender(domainId, filters), queryFn: () => api.get<TlsRptSenderSummary[]>(`/domains/${domainId}/dmarc/tls-rpt/by-sender${filters ? `?${filters}` : ""}`), enabled });
}
