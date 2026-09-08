import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { MtaStsBuilderData, TlsRptBuilderData } from "../api/dnsChecks";
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

export function useRequestHostedReportAddress(domainId: string, onSuccess?: (result: HostedReportAddressResult) => void, onError?: (error: Error) => void) {
  return useMutation({ mutationFn: () => api.post<HostedReportAddressResult>(`/domains/${domainId}/hosted-report-address`), onSuccess, onError });
}
