"""Minimal internal-only HTTP server that actually performs an update:
docker compose pull -> run migrate -> up -d api worker -> record the new
IMAGE_TAG in the env file -> recreate this updater itself. This is the one
piece of the updater feature with Docker socket access — mounted in
docker-compose.yml as /var/run/docker.sock, which is host-root-equivalent
(a container with it can control any container on the host and trivially
escalate to full host root). That's inherent to any updater architecture
that recreates containers this way, real watchtower included, not a flaw
specific to this design. Mitigated by keeping this container as small and
single-purpose as possible (stdlib only, no host port, shared-secret
auth even though it's not internet-reachable, read-only project mount
apart from the env file's IMAGE_TAG line, hardcoded command scope — never
touches db/resolver) — but
none of that eliminates the underlying risk: compromising this one
container (a bug here, a base-image CVE, a leaked UPDATER_SHARED_SECRET)
is equivalent to full host compromise, including whatever other
unrelated services run on the same host. Weigh that before deploying
this on a host that also carries workloads unrelated to this app.
"""

import hmac
import json
import os
import re
import shlex
import subprocess
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORKSPACE = "/workspace"
# The running app's own health endpoint, which reports its baked-in version.
# The api is this sidecar's only legitimate caller, so if it can't be reached
# the request didn't come through the admin console — refuse rather than guess.
APP_HEALTH_URL = os.environ.get("UPDATER_APP_HEALTH_URL", "http://api:8000/api/health")

# Mirrors backend/app/services/update_check.py's _VERSION_RE/_parse_version —
# duplicated rather than imported because this is a separate stdlib-only image.
_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-(beta|rc)(\d+))?$")
_CHANNEL_RANK = {"beta": 0, "rc": 1, None: 2}


def _parse_version(version: str) -> tuple[int, int, int, int, int] | None:
    match = _VERSION_RE.match(version)
    if match is None:
        return None
    major, minor, patch, channel, number = match.groups()
    return (int(major), int(minor), int(patch), _CHANNEL_RANK[channel], int(number) if number else 0)


def check_requested_version(requested: str, running: str | None) -> str | None:
    """Returns why an update to `requested` must be refused, or None if it's
    allowed. The shared secret is the only other gate in front of a
    container with Docker-socket (host-root) access, so this independently
    enforces what the backend already checks: a real release tag, strictly
    newer than what's running — never an arbitrary tag, and never a
    downgrade to an older (possibly since-patched) release."""
    requested_parsed = _parse_version(requested)
    if requested_parsed is None:
        return f"{requested!r} is not a release tag"
    if running is None:
        return "couldn't determine the running version"
    running_parsed = _parse_version(running)
    if running_parsed is None:
        return f"running version {running!r} is a development build — update it manually"
    if requested_parsed <= running_parsed:
        return f"{requested} is not newer than the running {running}"
    return None


def _running_version() -> str | None:
    try:
        with urllib.request.urlopen(APP_HEALTH_URL, timeout=5) as resp:
            return json.load(resp).get("version")
    except (OSError, ValueError):
        return None
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


def _own_label(label: str) -> str:
    container_id = os.environ.get("HOSTNAME", "")
    result = subprocess.run(
        ["docker", "inspect", container_id, "--format", f"{{{{ index .Config.Labels \"{label}\" }}}}"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _compose_project_name() -> str:
    """The project name compose actually used to create every sibling
    container (including this one) — read from this container's own
    label rather than inferred from a directory basename, since
    self-hosters clone this repo into all kinds of directory names and
    basename-inference would silently target the wrong (or no) project."""
    return _own_label("com.docker.compose.project")


def _project_host_dir() -> str:
    """Where the checkout lives on the HOST (mounted here as /workspace)."""
    return _own_label("com.docker.compose.project.working_dir")


def set_env_var(path: str, key: str, value: str) -> None:
    """Sets KEY=value in an env file, replacing an existing KEY= line or
    appending one, leaving every other line as it was. Written in place
    (not write-temp-then-rename) because the file is a single-file bind
    mount — a rename would swap out the inode the host never sees."""
    with open(path, "r+", encoding="utf-8") as f:
        lines = f.read().splitlines(keepends=True)
        new_line = f"{key}={value}\n"
        for i, line in enumerate(lines):
            if line.lstrip().startswith(f"{key}="):
                lines[i] = new_line
                break
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(new_line)
        f.seek(0)
        f.write("".join(lines))
        f.truncate()


def _run_update(version: str) -> None:
    project = _compose_project_name()
    compose = ["docker", "compose", "-p", project, "--env-file", f"{WORKSPACE}/{ENV_FILE}", "-f", f"{WORKSPACE}/{COMPOSE_FILE}"]
    # Overrides whatever IMAGE_TAG the env file might (not) define —
    # process environment takes precedence over --env-file in compose's
    # substitution order, so THIS is what actually controls which tag gets
    # pulled. Without it, `pull` always resolved to :latest regardless of
    # which version the admin console said was available — confirmed
    # live: harmless while :latest happened to get overwritten by every
    # prerelease (a since-fixed separate bug), broken for real once
    # :latest correctly went back to meaning "stable only" — pull would
    # then fetch an OLDER image than what's already running, missing
    # whatever migrations shipped after it.
    #
    # Deliberately NOT named APP_VERSION: that name is reserved for the
    # Dockerfile ARG baked into the image at build/publish time, which is
    # what the running app reports as its own version (app/config.py's
    # app_version). IMAGE_TAG only ever selects which tag `docker compose`
    # resolves for the image: line — it must never also become a container
    # env var, or it would win over (and permanently desync from) the
    # image's own correct baked-in APP_VERSION on every future container
    # recreation, since env_file-provided vars always override an image's
    # ENV instructions regardless of which image is actually running.
    env = {**os.environ, "IMAGE_TAG": version}
    try:
        subprocess.run(compose + ["pull", "api", "worker", "migrate", "updater"], cwd=WORKSPACE, env=env, check=True)
        # --no-deps on both: db/resolver are long-running, already-healthy
        # services with nothing to do with an app-code update — without
        # this, `run`/`up` evaluate the whole dependency graph and can
        # decide to recreate them too (confirmed live: this happened once,
        # tied to introducing this compose file's new structure — db came
        # back up cleanly on its existing volume, but a live db restart is
        # real disruption this shouldn't ever risk on a routine update).
        subprocess.run(compose + ["run", "--rm", "--no-deps", "migrate"], cwd=WORKSPACE, env=env, check=True)
        subprocess.run(compose + ["up", "-d", "--no-deps", "api", "worker"], cwd=WORKSPACE, env=env, check=True)
        print(f"update to {version} completed successfully", flush=True)
    except subprocess.CalledProcessError as exc:
        print(f"update to {version} failed: {exc}", flush=True)
        return

    # Persist the tag, or the next manual `docker compose up -d` would put
    # api/worker back on the old image — on top of the newer schema the
    # migrate step just applied.
    try:
        set_env_var(f"{WORKSPACE}/{ENV_FILE}", "IMAGE_TAG", version)
    except OSError as exc:
        print(f"couldn't record IMAGE_TAG={version} in {ENV_FILE} — set it by hand: {exc}", flush=True)

    _self_update(version, compose, env)


def _self_update(version: str, compose: list[str], env: dict) -> None:
    """Replaces this container with the new updater image. It can't
    recreate itself directly (stopping this container kills the compose
    process mid-way), so a throwaway container from the NEW image does it,
    after a short delay so this process can finish logging.

    That helper is a plain `docker run`, not `compose run`, and mounts the
    checkout at its real host path: compose resolves relative bind mounts
    (this service's own `.:/workspace`) against the project directory, and
    the Docker daemon reads the result as a host path — so the project
    directory compose sees has to BE the host path, or the new updater
    would come up with /workspace mounted from a nonexistent host dir."""
    try:
        config = subprocess.run(
            compose + ["config", "--format", "json"], cwd=WORKSPACE, env=env, check=True, capture_output=True, text=True
        )
        image = json.loads(config.stdout)["services"]["updater"]["image"]
        host_dir = _project_host_dir()
        recreate = [
            "docker", "compose", "-p", _compose_project_name(), "--project-directory", host_dir,
            "--env-file", f"{host_dir}/{ENV_FILE}", "-f", f"{host_dir}/{COMPOSE_FILE}",
            "up", "-d", "--no-deps", "updater",
        ]
        subprocess.run(
            ["docker", "run", "-d", "--rm",
             "-v", "/var/run/docker.sock:/var/run/docker.sock",
             "-v", f"{host_dir}:{host_dir}:ro",
             "-e", f"IMAGE_TAG={version}",
             "--entrypoint", "sh", image,
             "-c", f"sleep 3 && exec {shlex.join(recreate)}"],
            check=True,
        )
        print(f"updater self-update to {version} scheduled", flush=True)
    except (subprocess.CalledProcessError, KeyError, ValueError) as exc:
        print(f"updater self-update to {version} failed (api/worker are updated): {exc}", flush=True)


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

        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b"{}"
        try:
            version = json.loads(body)["version"]
            if not isinstance(version, str) or not version:
                raise ValueError
        except (json.JSONDecodeError, KeyError, ValueError):
            self.send_response(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": "missing or invalid 'version' in request body"}).encode())
            return

        refusal = check_requested_version(version, _running_version())
        if refusal is not None:
            self.send_response(409)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": refusal}).encode())
            return

        # Responds before starting the update, not after — `docker compose
        # up -d api worker` recreates the very api container whose request
        # triggered this, so a synchronous response would never make it
        # back on a successful update.
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"status": "started", "version": version}).encode())

        threading.Thread(target=_run_update, args=(version,), daemon=True).start()

    def log_message(self, format: str, *args) -> None:
        print(f"{self.address_string()} - {format % args}", flush=True)


if __name__ == "__main__":
    if not SHARED_SECRET:
        print("WARNING: UPDATER_SHARED_SECRET is unset — every request will be rejected", flush=True)
    print(f"configured for {COMPOSE_FILE} (env: {ENV_FILE})", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", 9999), Handler)
    print("updater listening on :9999", flush=True)
    server.serve_forever()
