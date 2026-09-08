import { useMutation, useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { OnboardingStatus } from "../api/onboarding";
import { queryKeys } from "./queryKeys";

export function useOnboardingStatus() {
  return useQuery({
    queryKey: queryKeys.onboarding.status,
    queryFn: () => api.get<OnboardingStatus>("/onboarding/status"),
  });
}

export function useGenerateHostedReportAddress(
  domainId: string,
  onSuccess?: (address: string) => void,
  onError?: (error: Error) => void,
) {
  return useMutation({
    mutationFn: () => api.post<{ hosted_report_address: string }>(`/domains/${domainId}/hosted-report-address`),
    onSuccess: (result) => onSuccess?.(result.hosted_report_address),
    onError,
  });
}
