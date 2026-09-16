import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { DmarcRecordDetail, DmarcReportsByDay, DmarcReportsGroupedRow, DmarcReportsSummary } from "../api/dmarc";
import { queryKeys } from "./queryKeys";
import { useCursorPage } from "./useCursorPage";

export function useDmarcReportsSummary(domainId: string, filters: string) {
  return useQuery({ queryKey: queryKeys.dmarcReports.summary(domainId, filters), queryFn: () => api.get<DmarcReportsSummary>(`/domains/${domainId}/dmarc/reports/summary${filters ? `?${filters}` : ""}`) });
}

export function useDmarcReportsByDay(domainId: string, filters: string, enabled: boolean) {
  return useCursorPage<DmarcReportsByDay>(
    queryKeys.dmarcReports.byDay(domainId, filters),
    (cursor) => {
      const qs = new URLSearchParams(filters);
      if (cursor) qs.set("before_id", cursor);
      return api.get<DmarcReportsByDay>(`/domains/${domainId}/dmarc/reports/by-day?${qs.toString()}`);
    },
    (lastPage) => {
      if (!lastPage.has_more) return undefined;
      const lastDay = lastPage.days[lastPage.days.length - 1];
      return lastDay?.rows[lastDay.rows.length - 1]?.record_id;
    },
    { enabled },
  );
}

export function useGroupedDmarcReports(domainId: string, grouping: string, filters: string) {
  return useQuery({
    queryKey: queryKeys.dmarcReports.grouped(domainId, grouping, filters),
    queryFn: () => api.get<DmarcReportsGroupedRow[]>(`/domains/${domainId}/dmarc/reports/grouped?by=${grouping}${filters ? `&${filters}` : ""}`),
    enabled: grouping !== "day",
  });
}

export function useDmarcRecordDetail(domainId: string, recordId: string | null) {
  return useQuery({
    queryKey: queryKeys.dmarcReports.detail(domainId, recordId),
    queryFn: () => api.get<DmarcRecordDetail>(`/domains/${domainId}/dmarc/records/${recordId}`),
    enabled: recordId !== null,
  });
}
