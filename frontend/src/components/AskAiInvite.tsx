import { useLocation } from "react-router-dom";
import { Sparkles, X } from "lucide-react";

import { useAuth } from "../auth/AuthContext";
import { useCurrentOrganization, useUpdateOrganization } from "../hooks/useOrganization";

/** Asks an org admin once whether to turn on Ask AI, while the
 *  organization hasn't answered (ask_ai_enabled is null). Closing it is a
 *  "no" and is saved, so it doesn't come back; the setting stays in
 *  Settings → General either way. Not on the onboarding wizard, which has
 *  its own step for this. */
export default function AskAiInvite() {
  const { user } = useAuth();
  const { data: org } = useCurrentOrganization({ enabled: !!user });
  const location = useLocation();
  const answer = useUpdateOrganization();

  if (!org || user?.role !== "org_admin" || org.ask_ai_enabled !== null || org.is_demo_read_only) return null;
  if (location.pathname.startsWith("/onboarding")) return null;

  const choose = (enabled: boolean) => answer.mutate({ name: org.name, ask_ai_enabled: enabled });

  return (
    <div className="ask-ai-invite" role="dialog" aria-label="Turn on Ask AI?">
      <button className="icon-btn ask-ai-invite-close" aria-label="No thanks" title="No thanks" onClick={() => choose(false)} disabled={answer.isPending}>
        <X size={14} />
      </button>
      <strong style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
        <Sparkles size={15} /> Get help from an AI assistant?
      </strong>
      <p>
        Adds an <em>Ask AI</em> button to issues: a ready-made question for Claude, ChatGPT or another assistant. You see
        it before it's sent; it never includes your organization's name or email addresses.
      </p>
      <div style={{ display: "flex", gap: "0.5rem" }}>
        <button className="btn btn--primary btn--sm" onClick={() => choose(true)} disabled={answer.isPending}>
          Turn on
        </button>
        <button className="btn btn--ghost btn--sm" onClick={() => choose(false)} disabled={answer.isPending}>
          No thanks
        </button>
      </div>
    </div>
  );
}
