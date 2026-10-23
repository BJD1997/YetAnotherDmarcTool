import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Mail, RefreshCw, ChevronDown, ChevronUp, History } from "lucide-react";
import { ApiError } from "../../api/client";
import { ReportFreshnessValue } from "../overview/widgets";
import {
  useMailboxConnection,
  useMailboxJobRuns,
  useResyncMailbox,
  useSetImapConnection,
  useSetMailboxConnection,
  useStartMicrosoftMailboxTest,
} from "../../hooks/useMailboxConnection";
import GoogleMailboxForm from "./GoogleMailboxForm";
import ImapMailboxForm from "./ImapMailboxForm";
import { useCurrentOrganization } from "../../hooks/useOrganization";
import { queryKeys } from "../../hooks/queryKeys";

// How long the page waits for a sync it started to show up before giving up.
const SYNC_WAIT_MS = 120_000;

// Shared between Settings.tsx and the onboarding wizard — one implementation
// of the mailbox-connect mutation/UI, not two.
export default function MailboxConnectionSection({ canManage }: { canManage: boolean }) {
  const { data: org } = useCurrentOrganization();

  // After a save/resync the sync runs on the worker, a few seconds to a
  // minute later. `syncFrom` is the last_sync_at from before the click
  // (undefined = not syncing): the sync is done once the server reports a
  // different one — compared with the server's own value, not the browser
  // clock, which can differ from it. Gives up after SYNC_WAIT_MS.
  const [syncFrom, setSyncFrom] = useState<string | null | undefined>(undefined);
  const syncing = syncFrom !== undefined;
  const queryClient = useQueryClient();

  const { data: connection, error } = useMailboxConnection({ refetchInterval: syncing ? 2000 : false });

  useEffect(() => {
    if (!syncing) return;
    const finish = () => {
      setSyncFrom(undefined);
      void queryClient.invalidateQueries({ queryKey: queryKeys.mailboxConnection.jobRuns });
    };
    if (connection && connection.last_sync_at !== syncFrom) {
      finish();
      return;
    }
    const timer = setTimeout(finish, SYNC_WAIT_MS);
    return () => clearTimeout(timer);
  }, [syncing, syncFrom, connection, queryClient]);

  const startWaiting = () => setSyncFrom(connection?.last_sync_at ?? null);

  const [mailbox, setMailbox] = useState("");
  const [editing, setEditing] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [showLinks, setShowLinks] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  // A Microsoft 365 mailbox or any IMAP mailbox. Organizations that don't
  // sign in with Microsoft first prove the mailbox's tenant with a test
  // sign-in (TenantProof), unless their Microsoft mailbox already has one.
  const canUseMicrosoft = !!org?.entra_tenant_id || !!org?.microsoft_mailbox_available;
  const [ticket, setTicket] = useState<string | null>(null);
  const googleDelegation = org?.google_mailbox ?? null;
  const kinds: Kind[] = [...(canUseMicrosoft ? (["microsoft"] as const) : []), "imap", ...(googleDelegation ? (["google"] as const) : [])];
  const showChoice = kinds.length > 1;
  const [choice, setChoice] = useState<Kind>("microsoft");
  // The first choice offered when the organization can't use Microsoft.
  const choiceShown: Kind = kinds.includes(choice) ? choice : kinds[0];
  const confirmId = useSetImapConnection(startWaiting, (err) =>
    setSaveError(err instanceof ApiError ? err.message : "couldn't save the mail server ID"),
  );

  const setConnection = useSetMailboxConnection(
    () => {
      setSaveError(null);
      setEditing(false);
      setMailbox("");
      startWaiting();
    },
    (err) => setSaveError(err instanceof ApiError ? err.message : "failed to save mailbox"),
  );

  const resync = useResyncMailbox(startWaiting);

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

        {showChoice && <MailboxChoice kinds={kinds} choice={choiceShown} onChange={setChoice} />}

        {choiceShown === "imap" ? (
          <div style={{ marginTop: "0.75rem" }}>
            <ImapMailboxForm onSaved={startWaiting} />
          </div>
        ) : choiceShown === "google" && googleDelegation ? (
          <div style={{ marginTop: "0.75rem" }}>
            <GoogleMailboxForm delegation={googleDelegation} onSaved={startWaiting} />
          </div>
        ) : (
        <>
        {org?.entra_tenant_id ? (
          <div style={{ marginTop: "0.75rem" }}>
            <div className="stat-tile-label">Step 1 — grant access</div>
            {consentCta}
          </div>
        ) : (
          <div style={{ marginTop: "0.75rem" }}>
            <div className="stat-tile-label">Step 1 — confirm your Microsoft tenant</div>
            <TenantProof onPassed={setTicket} />
          </div>
        )}

        <div style={{ marginTop: "0.9rem", paddingTop: "0.75rem", borderTop: "1px solid var(--border)" }}>
          <div className="stat-tile-label" style={{ marginBottom: "0.4rem" }}>
            Step 2 — connect the mailbox
          </div>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setConnection.mutate({ mailbox_address: mailbox, ...(ticket ? { ticket } : {}) });
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
            <button
              type="submit"
              className="btn btn--secondary btn--sm"
              disabled={setConnection.isPending || (!org?.entra_tenant_id && !ticket)}
            >
              Save &amp; start syncing
            </button>
          </form>
        </div>
        </>
        )}
        {saveError && <div style={{ marginTop: "0.5rem" }}>{saveError}</div>}
      </div>
    );
  }
  if (!connection) return null;

  const isImap = connection.kind === "imap";
  const isMicrosoft = connection.kind === "microsoft";
  const imap = connection.imap;
  const idUnconfirmed = isImap && !!imap && !imap.authserv_id;
  const ok = connection.consent_status === "granted" && connection.last_sync_status !== "error" && !idUnconfirmed;
  const editingImap = editing && choiceShown === "imap";
  const editingGoogle = editing && choiceShown === "google" && !!googleDelegation;
  return (
    <div className={`alert ${ok ? "alert--good" : "alert--warning"}`}>
      {editing && showChoice && <MailboxChoice kinds={kinds} choice={choiceShown} onChange={setChoice} />}
      {editingGoogle && googleDelegation ? (
        <div style={{ marginTop: "0.75rem" }}>
          <GoogleMailboxForm
            delegation={googleDelegation}
            current={connection.kind === "google" ? connection.mailbox_address : undefined}
            onSaved={() => {
              setEditing(false);
              startWaiting();
            }}
            onCancel={() => setEditing(false)}
          />
        </div>
      ) : editingImap ? (
        <div style={{ marginTop: showChoice ? "0.75rem" : 0 }}>
          <ImapMailboxForm
            current={imap}
            onSaved={() => {
              setEditing(false);
              startWaiting();
            }}
            onCancel={() => setEditing(false)}
          />
        </div>
      ) : editing ? (
        <>
        {!org?.entra_tenant_id && !isMicrosoft && (
          <div style={{ marginTop: "0.75rem", marginBottom: "0.5rem" }}>
            <TenantProof onPassed={setTicket} />
          </div>
        )}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setConnection.mutate({ mailbox_address: mailbox, ...(ticket ? { ticket } : {}) });
          }}
          className="field-row"
        >
          <input className="input" value={mailbox} onChange={(e) => setMailbox(e.target.value)} style={{ width: "260px" }} required />
          <button
            type="submit"
            className="btn btn--primary btn--sm"
            disabled={setConnection.isPending || (!org?.entra_tenant_id && !isMicrosoft && !ticket)}
          >
            Save
          </button>
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => setEditing(false)}>
            Cancel
          </button>
        </form>
        </>
      ) : (
        <>
          <div style={{ display: "flex", alignItems: "center", gap: "0.4rem", flexWrap: "wrap" }}>
            <Mail size={15} />
            {isImap && imap ? (
              <>
                IMAP mailbox: <strong>{connection.mailbox_address}</strong>
                <span className="muted">
                  {connection.mailbox_address !== imap.username.toLowerCase() && `signed in as ${imap.username}, `}
                  {`on ${imap.host}, ${imap.folder} → ${imap.processed_folder}`}
                </span>
              </>
            ) : connection.kind === "google" ? (
              <>
                Google Workspace mailbox: <strong>{connection.mailbox_address}</strong>
              </>
            ) : (
              <>
                Mailbox: <strong>{connection.mailbox_address}</strong>
                <span className="muted">— consent {connection.consent_status}</span>
              </>
            )}
            {connection.last_sync_at && (
              <span className="muted">, last synced {new Date(connection.last_sync_at).toLocaleString()}</span>
            )}
          </div>
          {connection.last_sync_status === "error" && connection.last_sync_error && !idUnconfirmed && (
            <div style={{ marginTop: "0.35rem" }}>— error: {connection.last_sync_error}</div>
          )}
          {idUnconfirmed && imap && (
            <div style={{ marginTop: "0.5rem" }}>
              <strong>Confirm your mail server's ID before reports are read.</strong>{" "}
              {imap.detected_authserv_id ? (
                <>
                  Your mail server signs its checks as <code>{imap.detected_authserv_id}</code>.{" "}
                  {canManage && (
                    <button
                      type="button"
                      className="btn btn--secondary btn--sm"
                      disabled={confirmId.isPending}
                      onClick={() =>
                        confirmId.mutate({
                          host: imap.host, port: imap.port, security: imap.security, username: imap.username,
                          report_address: imap.report_address, folder: imap.folder, processed_folder: imap.processed_folder,
                          authserv_id: imap.detected_authserv_id,
                        })
                      }
                    >
                      Use this
                    </button>
                  )}
                </>
              ) : (
                "It's detected from the first report that arrives; check back after the next sync."
              )}
            </div>
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
          {canManage && isMicrosoft && connection.last_sync_status === "error" && consentCta}
          {canManage && (
            <div className="chip-row" style={{ marginTop: "0.6rem" }}>
              <button className="btn btn--secondary btn--sm" onClick={() => resync.mutate()} disabled={resync.isPending || syncing}>
                <RefreshCw />
                {resync.isPending || syncing ? "Syncing…" : "Resync now"}
              </button>
              <button
                className="btn btn--ghost btn--sm"
                onClick={() => {
                  setMailbox(isMicrosoft ? connection.mailbox_address : "");
                  setChoice(connection.kind);
                  setEditing(true);
                }}
              >
                Change mailbox
              </button>
              {showChoice && (
                <button
                  className="btn btn--ghost btn--sm"
                  onClick={() => {
                    setMailbox("");
                    setChoice(kinds.find((k) => k !== connection.kind) ?? connection.kind);
                    setEditing(true);
                  }}
                >
                  Use another kind of mailbox
                </button>
              )}
              <button className="btn btn--ghost btn--sm" onClick={() => setShowHistory((v) => !v)}>
                <History />
                {showHistory ? "Hide" : "Recent syncs"}
              </button>
              {isMicrosoft && connection.last_sync_status !== "error" && (
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

// For an organization that doesn't sign in with Microsoft: a test sign-in
// with a Microsoft work account on one of its verified domains proves which
// tenant its report mailbox is in; the ticket goes along with the save.
function TenantProof({ onPassed }: { onPassed: (ticket: string) => void }) {
  const start = useStartMicrosoftMailboxTest();
  const [passed, setPassed] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    function onMessage(event: MessageEvent) {
      if (event.origin !== window.location.origin || event.data?.type !== "yadt-sign-in-test") return;
      if (event.data.ok && event.data.tenant_id) {
        setPassed(`Signed in as ${event.data.email}: tenant ${event.data.tenant_id}.`);
        setError(null);
        onPassed(event.data.ticket);
      } else if (!event.data.ok) {
        setPassed(null);
        setError(event.data.error ?? "The test sign-in failed.");
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [onPassed]);

  return (
    <div style={{ display: "grid", gap: "0.4rem", marginTop: "0.3rem" }}>
      <p className="section-hint" style={{ margin: 0 }}>
        Sign in once with a Microsoft work account of your organization (its email on one of your verified domains). Its
        tenant becomes the report mailbox's tenant; afterwards your Global Admin grants mail access.
      </p>
      <div>
        <button
          type="button"
          className="btn btn--secondary btn--sm"
          disabled={start.isPending}
          onClick={() => {
            setError(null);
            start.mutate(undefined, {
              onSuccess: ({ start_url }) => {
                window.open(start_url, "yadt-sign-in-test", "width=520,height=680");
              },
              onError: (err) => setError(err instanceof ApiError ? err.message : "couldn't start the test"),
            });
          }}
        >
          Test sign-in with Microsoft
        </button>
      </div>
      {passed && <div className="alert alert--good" style={{ margin: 0 }}>{passed}</div>}
      {error && <div className="alert alert--critical" style={{ margin: 0 }}>{error}</div>}
    </div>
  );
}

type Kind = "microsoft" | "imap" | "google";

const KIND_LABEL: Record<Kind, string> = {
  microsoft: "Microsoft 365 mailbox",
  imap: "Another mailbox (IMAP)",
  google: "Google Workspace mailbox",
};

function MailboxChoice({ kinds, choice, onChange }: { kinds: Kind[]; choice: Kind; onChange: (c: Kind) => void }) {
  return (
    <div className="chip-row" role="radiogroup" aria-label="Report mailbox" style={{ marginTop: "0.6rem" }}>
      {kinds.map((value) => (
        <label key={value} className="chip-row" style={{ gap: "0.3rem", cursor: "pointer" }}>
          <input type="radio" name="mailbox-kind" checked={choice === value} onChange={() => onChange(value)} />
          {KIND_LABEL[value]}
        </label>
      ))}
    </div>
  );
}

function RecentSyncs() {
  const { data, isLoading } = useMailboxJobRuns();

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
