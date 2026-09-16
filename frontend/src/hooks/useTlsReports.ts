import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { TlsRptReportRow, TlsRptSenderSummary, TlsRptSummary } from "../api/dmarc";
import { queryKeys } from "./queryKeys";
import { useCursorPage } from "./useCursorPage";

export function useTlsReportSummary(domainId: string, filters: string) {
  return useQuery({ queryKey: queryKeys.tlsReports.summary(domainId, filters), queryFn: () => api.get<TlsRptSummary>(`/domains/${domainId}/dmarc/tls-rpt/summary${filters ? `?${filters}` : ""}`) });
}

interface TlsReportsPage { reports: TlsRptReportRow[]; has_more: boolean }

export function useTlsReportRows(domainId: string, filters: string, enabled: boolean) {
  return useCursorPage<TlsReportsPage>(
    queryKeys.tlsReports.reports(domainId, filters),
    (cursor) => {
      const qs = new URLSearchParams(filters);
      if (cursor) qs.set("before_id", cursor);
      return api.get<TlsReportsPage>(`/domains/${domainId}/dmarc/tls-rpt/reports${qs.toString() ? `?${qs.toString()}` : ""}`);
    },
    (lastPage) => (lastPage.has_more ? lastPage.reports[lastPage.reports.length - 1]?.id : undefined),
    { enabled },
  );
}

export function useTlsReportsBySender(domainId: string, filters: string, enabled: boolean) {
  return useQuery({ queryKey: queryKeys.tlsReports.bySender(domainId, filters), queryFn: () => api.get<TlsRptSenderSummary[]>(`/domains/${domainId}/dmarc/tls-rpt/by-sender${filters ? `?${filters}` : ""}`), enabled });
}
