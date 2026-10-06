import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import type { AskAiHint } from "../api/overview";

export function useAskAiPrompt(domainId: string, hint: AskAiHint, issue: string | undefined, enabled: boolean) {
  const params = new URLSearchParams({ kind: hint.kind, domain_id: domainId });
  if (hint.subject) params.set("subject", hint.subject);
  if (issue) params.set("issue", issue.slice(0, 500));
  if (hint.period !== undefined) params.set("period", String(hint.period));
  return useQuery({
    queryKey: ["ask-ai", domainId, hint.kind, hint.subject, hint.period, issue],
    queryFn: () => api.get<{ prompt: string }>(`/ask-ai/prompt?${params}`),
    enabled,
    staleTime: 60_000,
  });
}
