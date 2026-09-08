import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { Organization } from "../api/types";
import { queryKeys } from "./queryKeys";

export function useCurrentOrganization() {
  return useQuery({
    queryKey: queryKeys.organization.current,
    queryFn: () => api.get<Organization>("/organizations/current"),
  });
}
