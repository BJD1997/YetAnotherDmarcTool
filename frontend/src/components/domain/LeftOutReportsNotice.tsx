import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../auth/AuthContext";
import { useLeftOutReports, type LeftOutReport } from "../../hooks/useLeftOutReports";

// Reports the organization's sender check leaves out of every view — listed
// here so they're reviewed rather than silently missing.
export default function LeftOutReportsNotice({ domainId, types }: { domainId: string; types: LeftOutReport["type"][] }) {
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const { data } = useLeftOutReports(domainId);
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
                    <td>{r.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="section-hint" style={{ margin: "0.5rem 0 0" }}>
            Left-out reports aren't counted anywhere.{" "}
            {user?.role === "org_admin" ? (
              <>
                To count reports like these, change the <Link to="/settings">report sender check</Link>.
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
