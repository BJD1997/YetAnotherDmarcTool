import { keepPreviousData, useMutation, useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { MtaStsBuilderData, SpfBuilderData, SpfEvaluation, TlsRptBuilderData } from "../api/dnsChecks";
import type { PolicyBuilderData } from "../api/policyBuilder";
import { queryKeys } from "./queryKeys";

export interface HostedReportAddressResult {
  hosted_report_address: string;
  authorization_record_status: "created" | "already_exists" | "unconfigured" | "error";
  authorization_record_detail: string | null;
}

export function useDmarcPolicyBuilder(domainId: string) {
  return useQuery({ queryKey: queryKeys.policyBuilder(domainId), queryFn: () => api.get<PolicyBuilderData>(`/domains/${domainId}/dmarc/policy-builder`) });
}

export function useTlsRptPolicyBuilder(domainId: string) {
  return useQuery({ queryKey: queryKeys.tlsRptBuilder(domainId), queryFn: () => api.get<TlsRptBuilderData>(`/domains/${domainId}/dns/tls-rpt-builder`) });
}

export function useMtaStsPolicyBuilder(domainId: string) {
  return useQuery({ queryKey: queryKeys.mtaStsBuilder(domainId), queryFn: () => api.get<MtaStsBuilderData>(`/domains/${domainId}/dns/mta-sts-builder`) });
}

export function useSpfBuilder(domainId: string) {
  return useQuery({ queryKey: queryKeys.spfBuilder(domainId), queryFn: () => api.get<SpfBuilderData>(`/domains/${domainId}/dns/spf-builder`) });
}

// Counts the draft record as the SPF check would. `record` is the
// debounced draft (null while there's nothing to count); the last count
// stays on screen while the next one loads.
export function useSpfEvaluation(domainId: string, record: string | null) {
  return useQuery({
    queryKey: queryKeys.spfEvaluation(domainId, record ?? ""),
    queryFn: () => api.post<SpfEvaluation>(`/domains/${domainId}/dns/spf-builder/evaluate`, { record }),
    enabled: record !== null,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}

export function useRequestHostedReportAddress(domainId: string, onSuccess?: (result: HostedReportAddressResult) => void, onError?: (error: Error) => void) {
  return useMutation({ mutationFn: () => api.post<HostedReportAddressResult>(`/domains/${domainId}/hosted-report-address`), onSuccess, onError });
}
