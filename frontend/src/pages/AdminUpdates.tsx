import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "../api/client";

interface UpdateStatus {
  running_version: string;
  latest_version: string | null;
  latest_release_url: string | null;
  latest_release_notes: string | null;
  latest_published_at: string | null;
  checked_at: string | null;
  check_error: string | null;
  include_prereleases: boolean;
  update_available: boolean;
  is_dev_build: boolean;
}

type UpdatePhase = "idle" | "checking" | "updating" | "success" | "error";

export default function AdminUpdates() {
  const queryClient = useQueryClient();
  const [reviewed, setReviewed] = useState(false);
  const [phase, setPhase] = useState<UpdatePhase>("idle");
  const [error, setError] = useState<string | null>(null);

  const { data: status, isLoading } = useQuery({
    queryKey: ["admin-updates"],
    queryFn: () => api.get<UpdateStatus>("/admin/updates"),
  });

  const setPrereleases = useMutation({
    mutationFn: (include: boolean) => api.patch<UpdateStatus>("/admin/updates", { include_prereleases: include }),
    onSuccess: async () => {
      // Flipping the toggle alone wouldn't change what "latest" means until
      // the next scheduled check (up to 6h away) — re-check immediately so
      // the page reflects the new channel right away.
      await api.post("/admin/updates/check-now");
      queryClient.invalidateQueries({ queryKey: ["admin-updates"] });
    },
  });

  async function checkNow() {
    setPhase("checking");
    setError(null);
    try {
      await api.post("/admin/updates/check-now");
      await queryClient.invalidateQueries({ queryKey: ["admin-updates"] });
      setPhase("idle");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "check failed");
      setPhase("error");
    }
  }

  async function updateNow() {
    setPhase("updating");
    setError(null);
    try {
      await api.post("/admin/updates/trigger");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "failed to trigger update");
      setPhase("error");
    }
    // The api container gets recreated mid-update — there's no response
    // to wait on for completion, so poll /api/health until it reports the
    // version we just triggered toward, treating fetch failures as "still
    // restarting" rather than errors.
  }

  useEffect(() => {
    if (phase !== "updating" || !status?.latest_version) return;
    const interval = setInterval(async () => {
      try {
        const health = await api.get<{ version: string }>("/health");
        if (health.version === status.latest_version) {
          setPhase("success");
          clearInterval(interval);
        }
      } catch {
        // api is briefly down mid-recreate — expected, keep polling
      }
    }, 5000);
    return () => clearInterval(interval);
  }, [phase, status?.latest_version]);

  if (isLoading || !status) {
    return (
      <section>
        <div className="page-header">
          <h1>Updates</h1>
        </div>
        <p className="muted">Loading…</p>
      </section>
    );
  }

  return (
    <section>
      <div className="page-header">
        <h1>Updates</h1>
      </div>

      <div className="card">
        <div className="stat-row" style={{ marginBottom: 0 }}>
          <div>
            <div className="stat-tile-label">Running version</div>
            <div className="stat-tile-value">{status.running_version}</div>
          </div>
          <div>
            <div className="stat-tile-label">Latest version</div>
            <div className="stat-tile-value">{status.latest_version ?? "—"}</div>
          </div>
        </div>
        {status.is_dev_build && (
          <p className="section-hint" style={{ marginTop: "0.75rem" }}>
            <span className="badge badge--neutral">development build</span>{" "}
            Off the release channel — updates are managed manually (rebuild &amp; redeploy), not from this page.
          </p>
        )}
        <p className="section-hint" style={{ marginTop: "0.75rem" }}>
          {status.checked_at ? `Last checked ${new Date(status.checked_at).toLocaleString()}` : "Never checked yet"}
          {status.check_error && <span className="badge badge--critical" style={{ marginLeft: "0.5rem" }}>check failed: {status.check_error}</span>}
        </p>
        <button className="btn btn--secondary btn--sm" onClick={checkNow} disabled={phase === "checking"}>
          {phase === "checking" ? "Checking…" : "Check now"}
        </button>

        <label
          className="section-hint"
          style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginTop: "0.9rem", paddingTop: "0.75rem", borderTop: "1px solid var(--border)" }}
        >
          <input
            type="checkbox"
            checked={status.include_prereleases}
            disabled={setPrereleases.isPending}
            onChange={(e) => setPrereleases.mutate(e.target.checked)}
          />
          Include prereleases (test builds tagged -rc/-beta, not recommended for production use)
        </label>
      </div>

      {status.update_available && (
        <div className="card" style={{ marginTop: "1rem" }}>
          <h3 className="section-title">
            Update available: {status.latest_version}
            {status.latest_release_url && (
              <a href={status.latest_release_url} target="_blank" rel="noopener noreferrer" style={{ marginLeft: "0.5rem", fontSize: "0.8rem" }}>
                view release
              </a>
            )}
          </h3>
          <p className="section-hint">Release notes:</p>
          <pre
            style={{
              whiteSpace: "pre-wrap",
              background: "var(--surface-raised)",
              padding: "0.75rem",
              borderRadius: "0.5rem",
              maxHeight: "20rem",
              overflow: "auto",
            }}
          >
            {status.latest_release_notes || "(no release notes provided)"}
          </pre>

          {phase === "success" ? (
            <div className="alert alert--good" style={{ marginTop: "0.75rem" }}>Updated successfully to {status.latest_version}.</div>
          ) : phase === "updating" ? (
            <div className="alert alert--neutral" style={{ marginTop: "0.75rem" }}>
              Update in progress — the app will restart shortly. This page will update automatically once it's back.
            </div>
          ) : (
            <>
              <label className="section-hint" style={{ display: "flex", alignItems: "center", gap: "0.5rem", marginTop: "0.75rem" }}>
                <input type="checkbox" checked={reviewed} onChange={(e) => setReviewed(e.target.checked)} />
                I've reviewed the release notes above
              </label>
              <button
                className="btn btn--primary btn--sm"
                style={{ marginTop: "0.5rem" }}
                disabled={!reviewed}
                onClick={updateNow}
              >
                Update now
              </button>
            </>
          )}
          {error && <div className="alert alert--critical" style={{ marginTop: "0.5rem" }}>{error}</div>}
        </div>
      )}
    </section>
  );
}
