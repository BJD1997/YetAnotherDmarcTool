"""In-app updates on Azure Container Apps — the counterpart of server.py for
deployments without a Docker socket. Runs as a manually triggered Container
Apps job (`<prefix>-updater`, see deploy/azure/modules/updater.bicep): the
api can only *start* this job (passing TARGET_VERSION); the job's own
managed identity, limited by a custom role to this resource group's
container apps and jobs, does the actual update through Azure Resource
Manager:

1. refuse anything but a newer release tag (server.check_requested_version);
2. move the migrate job to the new image and run it, waiting for success;
3. move the worker, then the api, to the new image (resolver sidecar too);
4. move this updater job to the new image, so the next update runs it.

Stdlib only, like server.py: this identity can change what runs in the
resource group, so every dependency is attack surface.
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request

from server import _parse_version, check_requested_version

ARM = "https://management.azure.com"
API_VERSION = "2024-03-01"
POLL_SECONDS = 10
MIGRATE_TIMEOUT_SECONDS = 30 * 60
PROVISION_TIMEOUT_SECONDS = 15 * 60


class UpdateError(Exception):
    pass


def log(message: str) -> None:
    print(message, flush=True)


def _http(method: str, url: str, *, headers: dict | None = None, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
            return response.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise UpdateError(f"{method} {url.split('?')[0]} failed ({exc.code}): {detail}") from exc


def managed_identity_token(client_id: str) -> str:
    """Token for this job's user-assigned identity, from the identity
    endpoint Container Apps provides to every replica."""
    endpoint, secret = os.environ.get("IDENTITY_ENDPOINT"), os.environ.get("IDENTITY_HEADER")
    if not endpoint or not secret:
        raise UpdateError("no managed identity endpoint (IDENTITY_ENDPOINT/IDENTITY_HEADER unset)")
    url = f"{endpoint}?resource={ARM}/&api-version=2019-08-01&client_id={client_id}"
    _status, body = _http("GET", url, headers={"X-IDENTITY-HEADER": secret})
    return body["access_token"]


def retag_template(template: dict, repos: list[str], version: str) -> dict:
    """Points every container whose image is one of `repos` at `version`,
    and drops any APP_VERSION env override so the image's own baked-in
    version is what the app reports. A fixed revisionSuffix is dropped too:
    reusing the current one would be refused as a duplicate revision."""
    template.pop("revisionSuffix", None)
    for key in ("containers", "initContainers"):
        for container in template.get(key) or []:
            repo, _, _tag = container.get("image", "").rpartition(":")
            if repo in repos:
                container["image"] = f"{repo}:{version}"
            if container.get("env"):
                container["env"] = [e for e in container["env"] if e.get("name") != "APP_VERSION"]
    return template


class Arm:
    def __init__(self, token: str, subscription: str, resource_group: str, sleep=time.sleep) -> None:
        self.headers = {"Authorization": f"Bearer {token}"}
        self.base = f"{ARM}/subscriptions/{subscription}/resourceGroups/{resource_group}/providers/Microsoft.App"
        self.sleep = sleep

    def _url(self, path: str) -> str:
        return f"{self.base}/{path}?api-version={API_VERSION}"

    def get(self, path: str) -> dict:
        return _http("GET", self._url(path), headers=self.headers)[1]

    def retag(self, path: str, repos: list[str], version: str) -> None:
        resource = self.get(path)
        template = retag_template(resource["properties"]["template"], repos, version)
        _http("PATCH", self._url(path), headers=self.headers, body={"properties": {"template": template}})
        self._wait_provisioned(path)

    def _wait_provisioned(self, path: str) -> None:
        deadline = time.monotonic() + PROVISION_TIMEOUT_SECONDS
        while True:
            state = self.get(path)["properties"].get("provisioningState")
            if state == "Succeeded":
                return
            if state in ("Failed", "Canceled"):
                raise UpdateError(f"{path} provisioning {state.lower()}")
            if time.monotonic() > deadline:
                raise UpdateError(f"{path} still {state} after {PROVISION_TIMEOUT_SECONDS // 60} minutes")
            self.sleep(POLL_SECONDS)

    def run_job(self, job: str) -> None:
        _status, started = _http("POST", self._url(f"jobs/{job}/start"), headers=self.headers, body={})
        execution = started["name"]
        log(f"  started {job} execution {execution}")
        deadline = time.monotonic() + MIGRATE_TIMEOUT_SECONDS
        while True:
            status = self.get(f"jobs/{job}/executions/{execution}")["properties"].get("status")
            if status == "Succeeded":
                return
            if status in ("Failed", "Stopped", "Degraded"):
                raise UpdateError(f"{job} execution {execution} {status.lower()} — see its logs in the Azure portal")
            if time.monotonic() > deadline:
                raise UpdateError(f"{job} execution {execution} still {status} after {MIGRATE_TIMEOUT_SECONDS // 60} minutes")
            self.sleep(POLL_SECONDS)


def running_version(health_url: str) -> str | None:
    try:
        return _http("GET", health_url)[1].get("version")
    except (UpdateError, OSError, ValueError):
        return None


def requested_update(request_url: str) -> tuple[str, bool]:
    """(version, rehearsal) as the admin requested it; ("", False) if none."""
    try:
        body = _http("GET", request_url)[1]
    except (UpdateError, OSError, ValueError):
        return "", False
    return body.get("version") or "", bool(body.get("rehearsal"))


def check_rehearsal(requested: str, running: str | None) -> str | None:
    """A rehearsal runs every step on the version already running — so it
    must be exactly that version (and a real release), never a way to switch
    versions."""
    if _parse_version(requested) is None:
        return f"{requested!r} is not a release tag"
    if requested != running:
        return f"a test update must target the running version ({running}), not {requested}"
    return None


def update(env: dict, arm: Arm | None = None) -> None:
    if env.get("TARGET_VERSION"):
        version, rehearsal = env["TARGET_VERSION"], env.get("REHEARSAL") == "1"
    else:
        version, rehearsal = requested_update(env["UPDATE_REQUEST_URL"])
    running = running_version(env["APP_HEALTH_URL"])
    refusal = check_rehearsal(version, running) if rehearsal else check_requested_version(version, running)
    if refusal is not None:
        raise UpdateError(f"refusing {'test ' if rehearsal else ''}update: {refusal}")

    repos = [r for r in env["IMAGE_REPOS"].split(",") if r]
    arm = arm or Arm(managed_identity_token(env["AZURE_CLIENT_ID"]), env["SUBSCRIPTION_ID"], env["RESOURCE_GROUP"])

    log(f"test update: every step on the running {version}, nothing changes" if rehearsal else f"updating to {version}")
    log("1/4 database migrations")
    arm.retag(f"jobs/{env['MIGRATE_JOB']}", repos, version)
    arm.run_job(env["MIGRATE_JOB"])
    log("2/4 worker")
    arm.retag(f"containerApps/{env['WORKER_APP']}", repos, version)
    log("3/4 api")
    arm.retag(f"containerApps/{env['API_APP']}", repos, version)
    log("4/4 updater")
    arm.retag(f"jobs/{env['UPDATER_JOB']}", repos, version)
    log(f"test update on {version} completed — all steps and permissions work" if rehearsal else f"update to {version} completed")


if __name__ == "__main__":
    try:
        update(dict(os.environ))
    except UpdateError as exc:
        log(f"update failed: {exc}")
        sys.exit(1)
