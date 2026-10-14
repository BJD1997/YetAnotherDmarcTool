import { useEffect, useState } from "react";
import { api, ApiError } from "../../api/client";
import { useAdminUpdateActions, useAdminUpdates, type UpdateStatus } from "../../hooks/useAdmin";

type UpdatePhase = "idle" | "checking" | "updating" | "success" | "error";

export default function AdminUpdates() {
  const [reviewed, setReviewed] = useState(false);
  const [phase, setPhase] = useState<UpdatePhase>("idle");
  const [error, setError] = useState<string | null>(null);

  const { data: status, isLoading } = useAdminUpdates();
  const { setPrereleases, checkNow: requestCheckNow, triggerUpdate } = useAdminUpdateActions();

  async function checkNow() {
    setPhase("checking");
    setError(null);
    try {
      await requestCheckNow();
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
      await triggerUpdate();
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

      {status.rehearsal_available && <TestUpdateSection resourceGroup={status.azure_resource_group} />}

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
          ) : !status.self_update_available ? (
            <ManualUpdateSteps status={status} />
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


// Shown instead of "Update now" where there's no updater sidecar to do it:
// Azure Container Apps (no Docker socket) and Portainer stacks.
function ManualUpdateSteps({ status }: { status: UpdateStatus }) {
  const version = status.latest_version ?? "";
  if (status.deployment_platform === "portainer") {
    return (
      <div style={{ marginTop: "0.75rem" }}>
        <p className="section-hint" style={{ marginBottom: "0.5rem" }}>
          In Portainer: open this stack, change <code>IMAGE_TAG</code> to <strong>{version}</strong> under Environment
          variables, and click <strong>Update the stack</strong> with <strong>Re-pull image</strong> on. That runs the
          database migrations and restarts the app.
        </p>
        <p className="section-hint" style={{ marginBottom: 0 }}>
          To update from here instead, set up Update now for Portainer: see{" "}
          <a href="https://github.com/BJD1997/YetAnotherDmarcTool/wiki/Deploying-with-Portainer" target="_blank" rel="noreferrer">
            Deploying with Portainer
          </a>
          .
        </p>
      </div>
    );
  }
  const azure = status.deployment_platform === "azure-container-apps";
  const command = azure
    ? [
        `az deployment group create -g ${status.azure_resource_group ?? "<resource group>"} \\`,
        `  --template-uri https://raw.githubusercontent.com/BJD1997/YetAnotherDmarcTool/${version}/deploy/azure/azuredeploy.json \\`,
        `  -p @<your parameters file> -p imageTag=${version}`,
      ].join("\n")
    : [
        `sed -i 's/^IMAGE_TAG=.*/IMAGE_TAG=${version}/' .env`,
        "docker compose pull",
        "docker compose run --rm migrate",
        "docker compose up -d",
      ].join("\n");

  return (
    <div style={{ marginTop: "0.75rem" }}>
      <p className="section-hint" style={{ marginBottom: "0.5rem" }}>
        {azure
          ? "This deployment updates by redeploying the Azure template with the new version, using the same parameters as your first deployment. That runs the database migrations and restarts the app:"
          : "This instance has no updater, so updates are done on the server. In the folder with your docker-compose.yml:"}
      </p>
      <pre
        style={{
          background: "var(--plane)",
          border: "1px solid var(--border)",
          padding: "0.75rem",
          borderRadius: "0.5rem",
          overflowX: "auto",
          fontSize: "0.8rem",
          margin: 0,
        }}
      >
        {command}
      </pre>
    </div>
  );
}


// Azure: runs every update step on the version already running, so the
// updater's Azure permissions can be checked before a real update exists.
function TestUpdateSection({ resourceGroup }: { resourceGroup: string | null }) {
  const { rehearse } = useAdminUpdateActions();
  const [state, setState] = useState<"idle" | "starting" | "started">("idle");
  const [error, setError] = useState<string | null>(null);

  async function start() {
    setState("starting");
    setError(null);
    try {
      await rehearse();
      setState("started");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "couldn't start the test update");
      setState("idle");
    }
  }

  return (
    <div className="card" style={{ marginTop: "1rem" }}>
      <h3 className="section-title">Test the updater</h3>
      <p className="section-hint">
        Runs a complete update on the version you're already running: database migrations, worker, api and the
        updater itself. Nothing changes, but every step and Azure permission a real update needs is used. The app
        restarts briefly.
      </p>
      {state === "started" ? (
        <div className="alert alert--good" style={{ margin: 0 }}>
          Test update started. It takes a few minutes. The result is in the Azure portal: {resourceGroup ?? "your resource group"} →
          the updater job → Execution history.
        </div>
      ) : (
        <button className="btn btn--secondary btn--sm" onClick={start} disabled={state === "starting"}>
          {state === "starting" ? "Starting…" : "Run test update"}
        </button>
      )}
      {error && <div className="alert alert--critical" style={{ marginTop: "0.5rem", marginBottom: 0 }}>{error}</div>}
    </div>
  );
}

