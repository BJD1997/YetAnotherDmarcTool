"""Asks the updater to install a release. Docker Compose: the `updater`
companion container's internal-only HTTP endpoint (updater/server.py) —
this module never touches the Docker socket itself. Azure Container Apps:
starts the updater job (updater/azure_update.py) through Azure Resource
Manager, with an identity that may only start that one job. Either way the
privileged part lives in the updater, not here."""

import os

import httpx

from app.config import settings


class UpdaterUnavailableError(Exception):
    pass


async def trigger_update(version: str) -> None:
    """`version` (e.g. "v0.1.2-rc4") is the specific tag to pull — the
    updater has no other way to know which release the admin console
    resolved as "latest", since a static IMAGE_TAG in .env only ever
    covers one fixed value (usually unset, i.e. :latest). See
    updater/server.py's _run_update for how this gets applied."""
    if settings.azure_self_update_available:
        await _start_azure_updater_job(version)
        return
    if not settings.updater_url or not settings.updater_shared_secret:
        raise UpdaterUnavailableError("updater isn't configured (UPDATER_URL/UPDATER_SHARED_SECRET unset)")

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{settings.updater_url}/trigger",
                headers={"X-Updater-Token": settings.updater_shared_secret},
                json={"version": version},
            )
    except httpx.HTTPError as exc:
        raise UpdaterUnavailableError(f"couldn't reach the updater: {exc}") from exc
    if resp.is_error:
        # The updater independently refuses non-release tags and downgrades
        # (see updater/server.py's check_requested_version) — pass its
        # reason through rather than a bare status code.
        try:
            reason = resp.json().get("error")
        except ValueError:
            reason = None
        raise UpdaterUnavailableError(f"updater refused the update ({resp.status_code}): {reason or resp.text[:200]}")


_ARM = "https://management.azure.com"
_ARM_API_VERSION = "2024-03-01"


async def _managed_identity_token(client: httpx.AsyncClient) -> str:
    endpoint, secret = os.environ.get("IDENTITY_ENDPOINT"), os.environ.get("IDENTITY_HEADER")
    if not endpoint or not secret:
        raise UpdaterUnavailableError("no managed identity available to start the updater")
    resp = await client.get(
        endpoint,
        params={"resource": f"{_ARM}/", "api-version": "2019-08-01", "client_id": settings.azure_update_client_id},
        headers={"X-IDENTITY-HEADER": secret},
    )
    if resp.is_error:
        raise UpdaterUnavailableError(f"couldn't get a managed identity token ({resp.status_code})")
    return resp.json()["access_token"]


async def _start_azure_updater_job(version: str) -> None:
    """Starts one execution of the updater job — nothing else: no overrides,
    so this identity (allowed only to start that one job) can't change what
    the job runs. The job reads the requested version back from
    GET /api/update-request and checks it itself (release tag, newer than
    running). `version` is only used for the error message."""
    job_url = f"{_ARM}{settings.azure_updater_job_id}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            headers = {"Authorization": f"Bearer {await _managed_identity_token(client)}"}
            started = await client.post(f"{job_url}/start", params={"api-version": _ARM_API_VERSION}, headers=headers)
    except httpx.HTTPError as exc:
        raise UpdaterUnavailableError(f"couldn't reach Azure to start the updater: {exc}") from exc
    if started.is_error:
        raise UpdaterUnavailableError(
            f"Azure refused to start the updater for {version} ({started.status_code}): {started.text[:200]}"
        )
