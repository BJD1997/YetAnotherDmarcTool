import { api } from "../api/client";
import { queryKeys } from "./queryKeys";
import { useCursorPage } from "./useCursorPage";

export interface SignInEvent {
  id: string;
  created_at: string;
  result: "success" | "failure" | "account_change";
  auth_method: "entra" | "local";
  email: string | null;
  failure_reason: string | null;
  actor_email: string | null;
  ip_address: string | null;
  user_agent: string | null;
}

interface SignInEventsPage { events: SignInEvent[]; has_more: boolean }

export function useSignInEvents(filters: string) {
  return useCursorPage<SignInEventsPage>(
    queryKeys.signInEvents(filters),
    (cursor) => {
      const qs = new URLSearchParams(filters);
      if (cursor) qs.set("before_id", cursor);
      return api.get<SignInEventsPage>(`/sign-in-events?${qs.toString()}`);
    },
    (lastPage) => (lastPage.has_more ? lastPage.events[lastPage.events.length - 1]?.id : undefined),
  );
}
