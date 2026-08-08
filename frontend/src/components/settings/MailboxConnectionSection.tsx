import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Mail, RefreshCw, ChevronDown, ChevronUp, History } from "lucide-react";
import { api, ApiError } from "../../api/client";
import type { Organization } from "../../api/types";
import type { MailboxConnectionStatus, MailboxJobRun } from "../../api/dmarc";
import { ReportFreshnessValue } from "../overview/widgets";

// Shared between Settings.tsx and the onboarding wizard — one implementation
// of the mailbox-connect mutation/UI, not two.
export default function MailboxConnectionSection({ canManage }: { canManage: boolean }) {
  const queryClient = useQueryClient();

  const { data: org } = useQuery({
    queryKey: ["organization", "current"],
    queryFn: () => api.get<Organization>("/organizations/current"),
  });

  // pollingSince drives a short-lived refetchInterval right after a
  // save/resync — catches the real sync result (kicked off immediately by
  // the backend, not on the next scheduler tick) much faster than a fixed
  // delay, and correctly waits for a FRESH last_sync_at rather than just
  // "any" status, so re-saving an already-connected mailbox doesn't read a
  // stale status left over from the previous connection.
  const [pollingSince, setPollingSince] = useState<number | null>(null);

  const { data: connection, error } = useQuery({
    queryKey: ["mailbox-connection"],
    queryFn: () => api.get<MailboxConnectionStatus>("/mailbox-connection"),
    retry: false,
    refetchInterval: (query) => {
      if (pollingSince === null) return false;
      const conn = query.state.data;
      const resolved = !!conn?.last_sync_at && new Date(conn.last_sync_at).getTime() >= pollingSince;
      if (resolved || Date.now() - pollingSince > 15000) return false;
      return 1500;
    },
  });

  const [mailbox, setMailbox] = useState("");
  const [editing, setEditing] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showLinks, setShowLinks] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  const setConnection = useMutation({
    mutationFn: () => api.put<MailboxConnectionStatus>("/mailbox-connection", { mailbox_address: mailbox }),
    onSuccess: () => {
      setSaveError(null);
      setEditing(false);
      setMailbox("");
      queryClient.invalidateQueries({ queryKey: ["mailbox-connection"] });
      queryClient.invalidateQueries({ queryKey: ["onboarding-status"] });
      setPollingSince(Date.now());
    },
    onError: (err) => setSaveError(err instanceof ApiError ? err.message : "failed to save mailbox"),
  });

  const resync = useMutation({
    mutationFn: () => api.post("/mailbox-connection/resync"),
    onSuccess: () => setPollingSince(Date.now()),
  });

  const consentLinks = org?.entra_consent_urls;
  const consentGuidance = consentLinks && (
    <div style={{ fontSize: "0.85rem", marginTop: "0.5rem" }}>
      Make sure your Global Admin has approved{" "}
      <a href={consentLinks.mail_access_consent_url} target="_blank" rel="noreferrer">
        mail access
      </a>{" "}
      (required) and, if your tenant blocks user sign-in consent,{" "}
      <a href={consentLinks.sso_consent_url} target="_blank" rel="noreferrer">
        dashboard sign-in
      </a>{" "}
      too.
    </div>
  );

  // The prominent version of the same guidance — Step 1's primary CTA, and
  // what re-surfaces (instead of the collapsed dropdown) when a sync
  // actually fails, so a broken connection points straight back at the
  // most likely cause.
  const consentCta = consentLinks && (
    <div style={{ marginTop: "0.5rem" }}>
      <p className="section-hint" style={{ marginTop: 0, marginBottom: "0.4rem" }}>
        Your Global Admin needs to approve mail access before a mailbox connection can sync.
      </p>
      <div className="chip-row">
        <a className="btn btn--primary btn--sm" href={consentLinks.mail_access_consent_url} target="_blank" rel="noreferrer">
          Grant mail access
        </a>
        <a className="btn btn--ghost btn--sm" href={consentLinks.sso_consent_url} target="_blank" rel="noreferrer">
          Grant dashboard sign-in
        </a>
      </div>
    </div>
  );

  const noConnectionYet = error instanceof ApiError && error.status === 404;

  if (noConnectionYet) {
    if (!canManage) {
      return (
        <div className="alert alert--warning">
          <Mail size={15} style={{ verticalAlign: "-2px", marginRight: "0.4rem" }} />
          No mailbox connection configured yet — ask your org admin to set one up.
        </div>
      );
    }
    return (
      <div className="alert alert--warning">
        <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
          <Mail size={15} />
          No mailbox connection configured yet.
        </div>

        <div style={{ marginTop: "0.75rem" }}>
          <div className="stat-tile-label">Step 1 — grant access</div>
          {consentCta}
        </div>

        <div style={{ marginTop: "0.9rem", paddingTop: "0.75rem", borderTop: "1px solid var(--border)" }}>
          <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
            Step 2 — connect the mailbox
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setConnection.mutate();
            }}
            className="field-row"
          >
            <input
              className="input"
              value={mailbox}
              onChange={(e) => setMailbox(e.target.value)}
              placeholder="dmarc-reports@yourdomain.com"
              style={{ width: "260px" }}
              required
            />
            <button type="submit" className="btn btn--secondary btn--sm" disabled={setConnection.isPending}>
              Save &amp; start syncing
            </button>
          </form>
        </div>
        {saveError && <div style={{ marginTop: "0.5rem" }}>{saveError}</div>}
      </div>
    );
  }
  if (!connection) return null;

  const ok = connection.consent_status === "granted" && connection.last_sync_status !== "error";
  return (
    <div className={`alert ${ok ? "alert--good" : "alert--warning"}`}>
      {editing ? (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setConnection.mutate();
          }}
          className="field-row"
        >
          <input className="input" value={mailbox} onChange={(e) => setMailbox(e.target.value)} style={{ width: "260px" }} required />
          <button type="submit" className="btn btn--primary btn--sm" disabled={setConnection.isPending}>
            Save
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setEditing(false)}>
            Cancel
          </button>
        </form>
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", flexWrap: "wrap" }}>
            <Mail size={15} />
            Mailbox: <strong>{connection.mailbox_address}</strong>
            <span className="muted">— consent {connection.consent_status}</span>
            {connection.last_sync_at && (
              <span className="muted">, last synced {new Date(connection.last_sync_at).toLocaleString()}</span>
            )}
          </div>
          {connection.last_sync_status === "error" && connection.last_sync_error && (
            <div style={{ marginTop: "0.35rem" }}>— error: {connection.last_sync_error}</div>
          )}

          <div className="chip-row" style={{ marginTop: "0.5rem", fontSize: "0.85rem" }}>
            <span className="muted">
              Last report received:{" "}
              {connection.last_report_at ? (
                <>
                  <ReportFreshnessValue
                    hours={(Date.now() - new Date(connection.last_report_at).getTime()) / 3_600_000}
                  />{" "}
                  ago
                </>
              ) : (
                "never"
              )}
            </span>
            {connection.last_run_stats && (
              <span className="muted">
                Last sync scanned {connection.last_run_stats.messages_seen} message
                {connection.last_run_stats.messages_seen === 1 ? "" : "s"}
                {connection.last_run_stats.errors > 0 && (
                  <span style={{ color: "var(--critical-text)" }}> ({connection.last_run_stats.errors} parser error{connection.last_run_stats.errors === 1 ? "" : "s"})</span>
                )}
              </span>
            )}
          </div>

          {/* Setting the mailbox is self-attested, not verified — a sync
              error very often just means the Entra consent steps were never
              actually completed, so the links that matter for fixing that
              need to stay reachable here too, not just before the mailbox
              was first configured. */}
          {canManage && connection.last_sync_status === "error" && consentCta}
          {canManage && (
            <div className="chip-row" style={{ marginTop: "0.6rem" }}>
              <button className="btn btn--secondary btn--sm" onClick={() => resync.mutate()} disabled={resync.isPending}>
                <RefreshCw />
                {resync.isPending || resync.isSuccess ? "Syncing…" : "Resync now"}
              </button>
              <button
                className="btn btn--ghost btn--sm"
                onClick={() => {
                  setMailbox(connection.mailbox_address);
                  setEditing(true);
                }}
              >
                Change mailbox
              </button>
              <button className="btn btn--ghost btn--sm" onClick={() => setShowHistory((v) => !v)}>
                <History />
                {showHistory ? "Hide" : "Recent syncs"}
              </button>
              {connection.last_sync_status !== "error" && (
                <button className="btn btn--ghost btn--sm" onClick={() => setShowLinks((v) => !v)}>
                  {showLinks ? <ChevronUp /> : <ChevronDown />}
                  Entra links
                </button>
              )}
            </div>
          )}
          {showLinks && connection.last_sync_status !== "error" && consentGuidance}
          {showHistory && <RecentSyncs />}
        </>
      )}
      {saveError && <div style={{ marginTop: "0.5rem" }}>{saveError}</div>}
    </div>
  );
}

function RecentSyncs() {
  const { data, isLoading } = useQuery({
    queryKey: ["mailbox-job-runs"],
    queryFn: () => api.get<MailboxJobRun[]>("/mailbox-connection/job-runs?limit=15"),
  });

  return (
    <div className="table-wrap" style={{ marginTop: "0.75rem" }}>
      {isLoading && <p className="muted">Loading…</p>}
      {!isLoading && (data ?? []).length === 0 && <p className="muted">No sync history yet.</p>}
      {!isLoading && (data ?? []).length > 0 && (
        <table className="table">
          <thead>
            <tr>
              <th>Started</th>
              <th>Status</th>
              <th>Messages</th>
              <th>Reports</th>
              <th>Errors</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((run) => (
              <tr key={run.id}>
                <td>{new Date(run.started_at).toLocaleString()}</td>
                <td>
                  <span className={`badge badge--${run.status === "success" ? "good" : run.status === "failure" ? "critical" : "warning"}`}>
                    {run.status}
                  </span>
                </td>
                <td className="num">{run.stats?.messages_seen ?? "—"}</td>
                <td className="num">{run.stats ? run.stats.aggregate_reports + run.stats.forensic_reports : "—"}</td>
                <td className="num" style={{ color: run.error_message || (run.stats?.errors ?? 0) > 0 ? "var(--critical-text)" : undefined }}>
                  {run.error_message ? <span title={run.error_message}>{run.stats?.errors ?? 1}</span> : (run.stats?.errors ?? 0)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
