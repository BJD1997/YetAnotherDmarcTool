import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";

import { api } from "../api/client";
import type { Organization } from "../api/types";
import { queryKeys } from "./queryKeys";

type CurrentOrganizationOptions = Pick<UseQueryOptions<Organization>, "enabled">;

export function useCurrentOrganization(options: CurrentOrganizationOptions = {}) {
  return useQuery({
    queryKey: queryKeys.organization.current,
    queryFn: () => api.get<Organization>("/organizations/current"),
    ...options,
  });
}

export function useUpdateOrganization(onSuccess?: () => void, onError?: (error: Error) => void) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: Pick<Organization, "name"> & Partial<Pick<Organization, "spf_all_qualifier_mode" | "hosted_mailbox_opt_in">>) =>
      api.patch<Organization>("/organizations/current", body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.organization.current });
      onSuccess?.();
    },
    onError,
  });
}
