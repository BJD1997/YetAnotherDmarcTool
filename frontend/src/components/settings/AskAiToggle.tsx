import { useState } from "react";

import { ApiError } from "../../api/client";
import type { Organization } from "../../api/types";
import { useUpdateOrganization } from "../../hooks/useOrganization";

// Shared by Settings → General and the onboarding wizard.
export const ASK_AI_EXPLANATION =
  "Adds an Ask AI button to issues, which opens a ready-made question in Claude or ChatGPT, or copies it for any other AI. You see the exact text before it's sent, under your own AI account. It holds only that issue's facts: domain names, public DNS records, sender names and IPs, and pass rates. Never your organization's name, email addresses or raw reports.";

export function AskAiToggle({ org }: { org: Organization }) {
  const [error, setError] = useState<string | null>(null);
  const setEnabled = useUpdateOrganization(
    () => setError(null),
    (err) => setError(err instanceof ApiError ? err.message : "failed to save"),
  );
  return (
    <div style={{ display: "grid", gap: "0.5rem" }}>
      <label style={{ display: "flex", gap: "0.5rem", alignItems: "center", cursor: "pointer" }}>
        <input
          type="checkbox"
          checked={org.ask_ai_enabled}
          disabled={setEnabled.isPending}
          onChange={(e) => setEnabled.mutate({ name: org.name, ask_ai_enabled: e.target.checked })}
        />
        Show Ask AI buttons for this organization
      </label>
      {error && <div className="alert alert--critical" style={{ margin: 0 }}>{error}</div>}
    </div>
  );
}

