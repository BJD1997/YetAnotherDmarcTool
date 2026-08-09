import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
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

export default function SignInEventsSection() {
  const [limit, setLimit] = useState(50);

  const { data: events, isLoading, isFetching, refetch } = useQuery({
    queryKey: ["sign-in-events", limit],
    queryFn: () => api.get<SignInEvent[]>(`/sign-in-events?limit=${limit}`),
  });

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: "0.6rem", marginBottom: "0.6rem" }}>
        <label className="muted" style={{ fontSize: "0.85rem", display: "flex", alignItems: "center", gap: "0.4rem" }}>
          Show last
          <select className="input" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
            <option value={20}>20</option>
            <option value={50}>50</option>
            <option value={100}>100</option>
            <option value={200}>200</option>
          </select>
        </label>
        <button className="btn btn--secondary btn--sm" onClick={() => refetch()} disabled={isFetching}>
          <RefreshCw />
          {isFetching ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {isLoading && <p className="muted">Loading…</p>}
      {events && events.length === 0 && <p className="empty-state">No sign-in activity yet.</p>}

      {events && events.length > 0 && (
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
    </div>
  );
}
