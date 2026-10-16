import { useState } from "react";
import { ApiError } from "../../api/client";
import { useSetGoogleMailbox, useTestGoogleMailbox } from "../../hooks/useMailboxConnection";

// A Google Workspace mailbox, read through this server's service account.
// The organization's Workspace admin grants it domain-wide delegation once;
// the mailbox must be on one of the organization's verified domains.
export default function GoogleMailboxForm({
  delegation,
  current,
  onSaved,
  onCancel,
}: {
  delegation: { client_id: string; scope: string };
  current?: string;
  onSaved: () => void;
  onCancel?: () => void;
}) {
  const [address, setAddress] = useState(current ?? "");
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const test = useTestGoogleMailbox();
  const save = useSetGoogleMailbox(onSaved, (err) => setError(err instanceof ApiError ? err.message : "couldn't save the mailbox"));

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        save.mutate(address.trim());
      }}
      style={{ display: "grid", gap: "0.6rem", maxWidth: "38rem" }}
    >
      <div className="section-hint" style={{ margin: 0 }}>
        Your Google Workspace admin grants access once: admin.google.com → Security → Access and data control → API
        controls → <strong>Manage domain-wide delegation</strong> → Add new, with
        <div style={{ margin: "0.3rem 0" }}>
          Client ID <code>{delegation.client_id}</code>
          <br />
          OAuth scope <code>{delegation.scope}</code>
        </div>
        Reports are then marked read and labelled "DMARC processed".
      </div>
      <label className="field-label">
        Mailbox
        <input
          className="input"
          type="email"
          value={address}
          onChange={(e) => {
            setAddress(e.target.value);
            setResult(null);
          }}
          placeholder="dmarc-reports@yourdomain.com"
          required
        />
        <span className="section-hint" style={{ margin: 0 }}>
          On one of your verified domains.
        </span>
      </label>
      {result && <div className="alert alert--good" style={{ margin: 0 }}>{result}</div>}
      {error && <div className="alert alert--critical" style={{ margin: 0 }}>{error}</div>}
      <div className="chip-row">
        <button
          type="button"
          className="btn btn--secondary btn--sm"
          disabled={test.isPending || !address.trim()}
          onClick={() => {
            setError(null);
            setResult(null);
            test.mutate(address.trim(), {
              onSuccess: (r) => setResult(`Connected: ${r.messages} message${r.messages === 1 ? "" : "s"} in the inbox.`),
              onError: (err) => setError(err instanceof ApiError ? err.message : "the connection test failed"),
            });
          }}
        >
          {test.isPending ? "Testing…" : "Test connection"}
        </button>
        <button type="submit" className="btn btn--primary btn--sm" disabled={save.isPending}>
          {save.isPending ? "Testing and saving…" : "Save & start syncing"}
        </button>
        {onCancel && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
