import { useState } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";
import { api } from "../../api/client";

interface SignInEvent {
  id: string;
  created_at: string;
  result: "success" | "failure";
  auth_method: "entra" | "local";
  email: string | null;
  failure_reason: string | null;
  ip_address: string | null;
  user_agent: string | null;
}

interface SignInEventsPage {
  events: SignInEvent[];
  has_more: boolean;
}

const LIMIT = 50;

export default function SignInEventsSection() {
  const [resultFilter, setResultFilter] = useState("");
  const [authMethodFilter, setAuthMethodFilter] = useState("");

  const params = new URLSearchParams({ limit: String(LIMIT) });
  if (resultFilter) params.set("result", resultFilter);
  if (authMethodFilter) params.set("auth_method", authMethodFilter);
  const filterQS = params.toString();

  const query = useInfiniteQuery({
    queryKey: ["sign-in-events", filterQS],
    queryFn: ({ pageParam }: { pageParam: string | undefined }) => {
      const qs = new URLSearchParams(filterQS);
      if (pageParam) qs.set("before_id", pageParam);
      return api.get<SignInEventsPage>(`/sign-in-events?${qs.toString()}`);
    },
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (lastPage) => {
      if (!lastPage.has_more) return undefined;
      return lastPage.events[lastPage.events.length - 1]?.id;
    },
  });

  const events = query.data?.pages.flatMap((page) => page.events) ?? [];

  return (
    <div>
      <div className="field-row" style={{ justifyContent: "flex-end", marginBottom: "0.6rem" }}>
        <select className="input" value={resultFilter} onChange={(e) => setResultFilter(e.target.value)}>
          <option value="">Any result</option>
          <option value="success">Success</option>
          <option value="failure">Failure</option>
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
                      <span className={`badge ${event.result === "success" ? "badge--good" : "badge--critical"}`}>{event.result}</span>
                    </td>
                    <td className="muted">{event.result === "failure" ? event.failure_reason : ""}</td>
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

      {query.hasNextPage && (
        <button
          className="btn btn--secondary"
          style={{ marginTop: "0.75rem" }}
          onClick={() => query.fetchNextPage()}
          disabled={query.isFetchingNextPage}
        >
          {query.isFetchingNextPage ? "Loading…" : "Load more"}
        </button>
      )}
    </div>
  );
}
