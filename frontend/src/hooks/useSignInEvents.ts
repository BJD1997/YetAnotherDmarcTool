import { useInfiniteQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { queryKeys } from "./queryKeys";

export interface SignInEvent {
  id: string;
  created_at: string;
  result: "success" | "failure";
  auth_method: "entra" | "local";
  email: string | null;
  failure_reason: string | null;
  ip_address: string | null;
  user_agent: string | null;
}

interface SignInEventsPage { events: SignInEvent[]; has_more: boolean }

export function useSignInEvents(filters: string) {
  return useInfiniteQuery({
    queryKey: queryKeys.signInEvents(filters),
    queryFn: ({ pageParam }: { pageParam: string | undefined }) => {
      const qs = new URLSearchParams(filters);
      if (pageParam) qs.set("before_id", pageParam);
      return api.get<SignInEventsPage>(`/sign-in-events?${qs.toString()}`);
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => lastPage.has_more ? lastPage.events[lastPage.events.length - 1]?.id : undefined,
  });
}
