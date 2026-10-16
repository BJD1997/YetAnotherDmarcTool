import { useState } from "react";
import { ApiError } from "../../api/client";
import type { ImapSettingsIn, ImapSettingsOut } from "../../api/dmarc";
import { useSetImapConnection, useTestImap } from "../../hooks/useMailboxConnection";

const DEFAULT_PORT = { ssl: 993, starttls: 143 } as const;

// Any IMAP mailbox as the organization's report mailbox. Saving tests the
// connection first, so settings that don't work are never saved. After a
// report is stored, its message is marked read and moved to the processed
// folder.
export default function ImapMailboxForm({
  current,
  onSaved,
  onCancel,
}: {
  current?: ImapSettingsOut | null;
  onSaved: () => void;
  onCancel?: () => void;
}) {
  const [host, setHost] = useState(current?.host ?? "");
  const [security, setSecurity] = useState<"ssl" | "starttls">(current?.security ?? "ssl");
  const [port, setPort] = useState<number>(current?.port ?? DEFAULT_PORT.ssl);
  const [username, setUsername] = useState(current?.username ?? "");
  const [password, setPassword] = useState("");
  // Only shown filled in when it differs from the username.
  const [reportAddress, setReportAddress] = useState(
    current && current.report_address !== current.username.toLowerCase() ? current.report_address : "",
  );
  const [folder, setFolder] = useState(current?.folder ?? "INBOX");
  const [processedFolder, setProcessedFolder] = useState(current?.processed_folder ?? "DMARC processed");
  const [authservId, setAuthservId] = useState(current?.authserv_id ?? current?.detected_authserv_id ?? "");
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const keepsPassword = !!current?.password_set && current.host === host.trim().toLowerCase() && current.username === username.trim();
  const body = (): ImapSettingsIn => ({
    host: host.trim(),
    port,
    security,
    username: username.trim(),
    ...(password ? { password } : {}),
    ...(reportAddress.trim() ? { report_address: reportAddress.trim() } : {}),
    folder: folder.trim(),
    processed_folder: processedFolder.trim(),
    authserv_id: authservId.trim() || null,
  });
  const message = (err: Error, fallback: string) => (err instanceof ApiError ? err.message : fallback);

  const test = useTestImap();
  const save = useSetImapConnection(onSaved, (err) => setError(message(err, "couldn't save the mailbox")));

  function runTest() {
    setError(null);
    setResult(null);
    test.mutate(body(), {
      onSuccess: (r) => {
        if (r.detected_authserv_id && !authservId.trim()) setAuthservId(r.detected_authserv_id);
        setResult(
          `Connected: ${r.messages} message${r.messages === 1 ? "" : "s"} in ${folder.trim()}.` +
            (r.detected_authserv_id ? ` Your mail server's ID is ${r.detected_authserv_id}.` : " The folder is empty, so the server's ID can't be detected yet."),
        );
      },
      onError: (err) => setError(message(err, "the connection test failed")),
    });
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        save.mutate(body());
      }}
      style={{ display: "grid", gap: "0.6rem", maxWidth: "36rem" }}
    >
      <div className="field-row">
        <label className="field-label" style={{ flex: "1 1 14rem" }}>
          IMAP server
          <input className="input" value={host} onChange={(e) => setHost(e.target.value)} placeholder="imap.example.com" required />
        </label>
        <label className="field-label">
          Security
          <select
            className="input"
            value={security}
            onChange={(e) => {
              const next = e.target.value as "ssl" | "starttls";
              if (port === DEFAULT_PORT[security]) setPort(DEFAULT_PORT[next]);
              setSecurity(next);
            }}
          >
            <option value="ssl">SSL/TLS</option>
            <option value="starttls">STARTTLS</option>
          </select>
        </label>
        <label className="field-label" style={{ width: "6rem" }}>
          Port
          <input className="input" type="number" min={1} max={65535} value={port} onChange={(e) => setPort(Number(e.target.value))} required />
        </label>
      </div>
      <div className="field-row">
        <label className="field-label" style={{ flex: "1 1 14rem" }}>
          Username
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="dmarc-reports@example.com" autoComplete="off" required />
        </label>
        <label className="field-label" style={{ flex: "1 1 12rem" }}>
          Password
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={keepsPassword ? "unchanged" : "app password"}
            autoComplete="new-password"
            required={!keepsPassword}
          />
        </label>
      </div>
      <label className="field-label">
        Report address
        <input
          className="input"
          type="email"
          value={reportAddress}
          onChange={(e) => setReportAddress(e.target.value)}
          placeholder="same as the username"
        />
        <span className="section-hint" style={{ margin: 0 }}>
          The address in your DMARC rua= record. Only needed when you sign in with something other than this address.
        </span>
      </label>
      <div className="field-row">
        <label className="field-label" style={{ flex: "1 1 12rem" }}>
          Reports arrive in
          <input className="input" value={folder} onChange={(e) => setFolder(e.target.value)} required />
        </label>
        <label className="field-label" style={{ flex: "1 1 12rem" }}>
          Move processed reports to
          <input className="input" value={processedFolder} onChange={(e) => setProcessedFolder(e.target.value)} required />
        </label>
      </div>
      <label className="field-label">
        Mail server ID
        <input className="input" value={authservId} onChange={(e) => setAuthservId(e.target.value)} placeholder="filled in by Test connection" />
        <span className="section-hint" style={{ margin: 0 }}>
          The name your mail server signs its checks with (the start of its Authentication-Results header). Only its
          checks count, so nobody can fake a report's origin. Reports are read once this is set.
        </span>
      </label>
      <p className="section-hint" style={{ margin: 0 }}>
        Use a mailbox just for reports, and an app password where your provider offers one. Each report is marked read
        and moved to the folder above; the folder is created if it doesn't exist.
      </p>
      {result && <div className="alert alert--good" style={{ margin: 0 }}>{result}</div>}
      {error && <div className="alert alert--critical" style={{ margin: 0 }}>{error}</div>}
      <div className="chip-row">
        <button type="button" className="btn btn--secondary btn--sm" onClick={runTest} disabled={test.isPending || !host || !username}>
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
