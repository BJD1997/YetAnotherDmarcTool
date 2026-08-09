"""Minimal internal-only HTTP server that actually performs an update:
docker compose pull -> run migrate -> up -d api worker. This is the one
piece of the updater feature with Docker socket access — mounted in
docker-compose.yml as /var/run/docker.sock, which is host-root-equivalent
(a container with it can control any container on the host and trivially
escalate to full host root). That's inherent to any updater architecture
that recreates containers this way, real watchtower included, not a flaw
specific to this design. Mitigated by keeping this container as small and
single-purpose as possible (stdlib only, no host port, shared-secret
auth even though it's not internet-reachable, read-only project mount,
hardcoded command scope — never touches db/resolver or itself) — but
none of that eliminates the underlying risk: compromising this one
container (a bug here, a base-image CVE, a leaked UPDATER_SHARED_SECRET)
is equivalent to full host compromise, including whatever other
unrelated services run on the same host. Weigh that before deploying
this on a host that also carries workloads unrelated to this app.
"""

import hmac
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORKSPACE = "/workspace"
SHARED_SECRET = os.environ.get("UPDATER_SHARED_SECRET", "")


def _compose_project_name() -> str:
    """The project name compose actually used to create every sibling
    container (including this one) — read from this container's own
    label rather than inferred from a directory basename, since
    self-hosters clone this repo into all kinds of directory names and
    basename-inference would silently target the wrong (or no) project."""
    container_id = os.environ.get("HOSTNAME", "")
    result = subprocess.run(
        ["docker", "inspect", container_id, "--format", "{{ index .Config.Labels \"com.docker.compose.project\" }}"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _run_update() -> None:
    project = _compose_project_name()
    compose = ["docker", "compose", "-p", project, "-f", f"{WORKSPACE}/docker-compose.yml"]
    try:
        subprocess.run(compose + ["pull", "api", "worker", "migrate"], cwd=WORKSPACE, check=True)
        # --no-deps on both: db/resolver are long-running, already-healthy
        # services with nothing to do with an app-code update — without
        # this, `run`/`up` evaluate the whole dependency graph and can
        # decide to recreate them too (confirmed live: this happened once,
        # tied to introducing this compose file's new structure — db came
        # back up cleanly on its existing volume, but a live db restart is
        # real disruption this shouldn't ever risk on a routine update).
        subprocess.run(compose + ["run", "--rm", "--no-deps", "migrate"], cwd=WORKSPACE, check=True)
        subprocess.run(compose + ["up", "-d", "--no-deps", "api", "worker"], cwd=WORKSPACE, check=True)
        print("update completed successfully", flush=True)
    except subprocess.CalledProcessError as exc:
        print(f"update failed: {exc}", flush=True)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/trigger":
            self.send_response(404)
            self.end_headers()
            return

        token = self.headers.get("X-Updater-Token", "")
        if not SHARED_SECRET or not hmac.compare_digest(token, SHARED_SECRET):
            self.send_response(401)
            self.end_headers()
            return

        # Responds before starting the update, not after — `docker compose
        # up -d api worker` recreates the very api container whose request
        # triggered this, so a synchronous response would never make it
        # back on a successful update.
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "started"}).encode())

        threading.Thread(target=_run_update, daemon=True).start()

    def log_message(self, format: str, *args) -> None:
        print(f"{self.address_string()} - {format % args}", flush=True)


if __name__ == "__main__":
    if not SHARED_SECRET:
        print("WARNING: UPDATER_SHARED_SECRET is unset — every request will be rejected", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", 9999), Handler)
    print("updater listening on :9999", flush=True)
    server.serve_forever()
