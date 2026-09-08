import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { Domain } from "../api/types";
import { queryKeys } from "./queryKeys";

export function useDomains() {
  return useQuery({
    queryKey: queryKeys.domains.all,
    queryFn: () => api.get<Domain[]>("/domains"),
  });
}
