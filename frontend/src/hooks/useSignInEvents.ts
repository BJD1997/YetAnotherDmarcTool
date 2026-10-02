import { api } from "../api/client";
import { queryKeys } from "./queryKeys";
import { useCursorPage } from "./useCursorPage";

export interface SignInEvent {
  id: string;
  created_at: string;
  result: "success" | "failure" | "account_change";
  auth_method: "entra" | "local" | "platform_admin";
  email: string | null;
  failure_reason: string | null;
  actor_email: string | null;
  ip_address: string | null;
  user_agent: string | null;
}

interface SignInEventsPage { events: SignInEvent[]; has_more: boolean }

// endpoint: "/sign-in-events" (the org's own log) or
// "/admin/sign-in-events" (break-glass admin sign-ins).
export function useSignInEvents(filters: string, endpoint = "/sign-in-events") {
  return useCursorPage<SignInEventsPage>(
    queryKeys.signInEvents(`${endpoint}?${filters}`),
    (cursor) => {
      const qs = new URLSearchParams(filters);
      if (cursor) qs.set("before_id", cursor);
      return api.get<SignInEventsPage>(`${endpoint}?${qs.toString()}`);
    },
    (lastPage) => (lastPage.has_more ? lastPage.events[lastPage.events.length - 1]?.id : undefined),
  );
}
