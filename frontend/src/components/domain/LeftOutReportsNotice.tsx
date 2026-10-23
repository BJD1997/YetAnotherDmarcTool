import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { useLeftOutReports, type LeftOutReport } from "../../hooks/useLeftOutReports";
import { ApiError } from "../../api/client";
import { useTrustReporter } from "../../hooks/useTrustedReporters";

// Reports the organization's sender check leaves out of every view — listed
// here so they're reviewed rather than silently missing.
export default function LeftOutReportsNotice({ domainId, types }: { domainId: string; types: LeftOutReport["type"][] }) {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const { data } = useLeftOutReports(domainId);
  const [trustError, setTrustError] = useState<string | null>(null);
  const trust = useTrustReporter((err) => setTrustError(err instanceof ApiError ? err.message : "couldn't trust this reporter"));
  const canManage = user?.role === "org_admin";
  const reports = (data ?? []).filter((r) => types.includes(r.type));
  if (reports.length === 0) return null;

  return (
    <div className="alert alert--warning">
      <div className="chip-row" style={{ justifyContent: "space-between" }}>
        <span>
          {reports.length === 1 ? "1 report was" : `${reports.length} reports were`} left out because the sender couldn't be
          verified.
        </span>
        <button type="button" className="btn btn--ghost btn--sm" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
          {open ? "Hide" : "Review"}
        </button>
      </div>
      {open && (
        <>
          <div className="table-wrap" style={{ marginTop: "0.6rem" }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Reporter</th>
                  <th>Period</th>
                  <th>Received</th>
                  <th>Why it was left out</th>
                </tr>
              </thead>
              <tbody>
                {reports.map((r) => (
                  <tr key={r.id}>
                    <td>{r.type}</td>
                    <td>{r.reporter ?? "—"}</td>
                    <td className="num">{new Date(r.period_start).toLocaleDateString()}</td>
                    <td className="num">{new Date(r.received_at).toLocaleString()}</td>
                    <td>
                      {r.reason}
                      {canManage && r.trustable && r.sender_domain && (
                        <button
                          type="button"
                          className="btn btn--ghost btn--sm"
                          style={{ marginLeft: "0.5rem" }}
                          disabled={trust.isPending}
                          onClick={() => trust.mutate(r.sender_domain!)}
                        >
                          Trust {r.sender_domain}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {trustError && <div className="alert alert--critical" style={{ marginTop: "0.5rem" }}>{trustError}</div>}
          <p className="section-hint" style={{ margin: "0.5rem 0 0" }}>
            Left-out reports aren't counted anywhere.{" "}
            {canManage ? (
              <>
                Only trust a reporter you recognize as a mail provider that receives your mail — its reports will
                count from then on. To count other reports like these, change the{" "}
                <Link to="/settings">report sender check</Link>.
              </>
            ) : (
              "An org admin can change which reports count in Settings."
            )}
          </p>
        </>
      )}
    </div>
  );
}
