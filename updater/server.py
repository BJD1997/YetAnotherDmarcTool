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
# Both docker-compose.yml (the real instance) and docker-compose.demo.yml
# (the isolated public demo) share this same image and mount the same
# read-only /workspace (the whole repo checkout, since both files live
# side by side there) — this MUST be set per-deployment. Getting it wrong
# doesn't fail loudly: `-p <this project> -f <wrong file>` is still valid
# docker compose invocation, it just applies the WRONG file's service
# definitions (ports, images, env_file) under this project's name, which
# for prod vs demo means colliding host ports and cross-deployment secrets.
COMPOSE_FILE = os.environ.get("UPDATER_COMPOSE_FILE", "docker-compose.yml")
# Just as important and easy to miss: `docker compose` auto-loads a file
# literally named `.env` from the project directory for ${VAR} substitution
# in the compose YAML itself — regardless of which -f file is given. Since
# both .env and .env.demo live in the same /workspace, an update running
# under the demo project without an explicit --env-file would silently
# substitute the REAL instance's secrets (POSTGRES_PASSWORD,
# DMARC_APP_DB_PASSWORD, ...) into the demo's containers instead of its own
# — confirmed live: this had already happened before this variable existed,
# and demo's actual Postgres roles had to be rotated to match .env.demo
# for real once the substitution was fixed.
ENV_FILE = os.environ.get("UPDATER_ENV_FILE", ".env")


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
    compose = ["docker", "compose", "-p", project, "--env-file", f"{WORKSPACE}/{ENV_FILE}", "-f", f"{WORKSPACE}/{COMPOSE_FILE}"]
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
    print(f"configured for {COMPOSE_FILE} (env: {ENV_FILE})", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", 9999), Handler)
    print("updater listening on :9999", flush=True)
    server.serve_forever()
