import { useState } from "react";
import { useSignInEvents, type SignInEvent } from "../../hooks/useSignInEvents";
import { LoadMoreButton } from "../shared/LoadMoreButton";

const ACCOUNT_CHANGES: Record<string, string> = {
  password_changed: "Changed their password",
  authenticator_replaced: "Replaced their authenticator",
  password_reset_by_admin: "Password reset",
  mfa_reset_by_admin: "Two-factor authentication reset",
  password_reset_by_platform_admin: "Password reset by platform admin",
  mfa_reset_by_platform_admin: "Two-factor authentication reset by platform admin",
};

function reasonText(event: SignInEvent): string {
  if (event.result === "failure") return event.failure_reason ?? "";
  if (event.result !== "account_change") return "";
  const action = ACCOUNT_CHANGES[event.failure_reason ?? ""] ?? event.failure_reason ?? "";
  return event.actor_email ? `${action} (by ${event.actor_email})` : action;
}

const RESULT_BADGE: Record<SignInEvent["result"], { className: string; label: string }> = {
  success: { className: "badge--good", label: "success" },
  failure: { className: "badge--critical", label: "failure" },
  account_change: { className: "badge--neutral", label: "account change" },
};

export default function SignInEventsSection() {
  const [resultFilter, setResultFilter] = useState("");
  const [authMethodFilter, setAuthMethodFilter] = useState("");

  // No explicit limit param — the backend's own default (50, see
  // /sign-in-events' `limit: int = Query(50, ...)`) is relied on, matching
  // the other four paginated hooks (DMARC by-day, TLS-RPT, Admin
  // Organizations, Admin Job Runs), none of which set it explicitly either.
  const params = new URLSearchParams();
  if (resultFilter) params.set("result", resultFilter);
  if (authMethodFilter) params.set("auth_method", authMethodFilter);
  const filterQS = params.toString();

  const query = useSignInEvents(filterQS);

  const events = query.data?.pages.flatMap((page) => page.events) ?? [];

  return (
    <div>
      <div className="field-row" style={{ justifyContent: "flex-end", marginBottom: "0.6rem" }}>
        <select className="input" value={resultFilter} onChange={(e) => setResultFilter(e.target.value)}>
          <option value="">Any result</option>
          <option value="success">Success</option>
          <option value="failure">Failure</option>
          <option value="account_change">Account change</option>
        </select>
        <select className="input" value={authMethodFilter} onChange={(e) => setAuthMethodFilter(e.target.value)}>
          <option value="">Any method</option>
          <option value="entra">Microsoft</option>
          <option value="local">Local</option>
        </select>
      </div>

      {query.isLoading && <p className="muted">Loading…</p>}
      {query.isSuccess && events.length === 0 && <p className="empty-state">No sign-in activity matches these filters.</p>}

      {events.length > 0 && (
        <div className="card" style={{ padding: 0 }}>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>Method</th>
                  <th>Result</th>
                  <th>Reason</th>
                  <th>IP address</th>
                  <th>User agent</th>
                  <th>When</th>
                </tr>
              </thead>
              <tbody>
                {events.map((event) => (
                  <tr key={event.id} style={event.result === "failure" ? { background: "var(--critical-wash)" } : undefined}>
                    <td>{event.email ?? "—"}</td>
                    <td>
                      <span className="badge badge--neutral">{event.auth_method === "entra" ? "Microsoft" : "local"}</span>
                    </td>
                    <td>
                      <span className={`badge ${RESULT_BADGE[event.result].className}`}>{RESULT_BADGE[event.result].label}</span>
                    </td>
                    <td className="muted">{reasonText(event)}</td>
                    <td className="muted">{event.ip_address ?? "—"}</td>
                    <td className="muted" style={{ maxWidth: "16rem", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={event.user_agent ?? undefined}>
                      {event.user_agent ?? "—"}
                    </td>
                    <td style={{ whiteSpace: "nowrap" }} className="num">
                      {new Date(event.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <LoadMoreButton
        hasNextPage={query.hasNextPage}
        isFetchingNextPage={query.isFetchingNextPage}
        onClick={() => query.fetchNextPage()}
      />
    </div>
  );
}
