import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { TeamMember } from "../api/types";
import { queryKeys } from "./queryKeys";

export function useUsers() {
  return useQuery({
    queryKey: queryKeys.users.all,
    queryFn: () => api.get<TeamMember[]>("/users"),
  });
}
