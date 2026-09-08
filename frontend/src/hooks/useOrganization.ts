import { useQuery, type UseQueryOptions } from "@tanstack/react-query";

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
