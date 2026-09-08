import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { OnboardingStatus } from "../api/onboarding";
import { queryKeys } from "./queryKeys";

export function useOnboardingStatus() {
  return useQuery({
    queryKey: queryKeys.onboarding.status,
    queryFn: () => api.get<OnboardingStatus>("/onboarding/status"),
  });
}
