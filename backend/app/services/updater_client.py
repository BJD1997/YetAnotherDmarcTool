"""Talks to the `updater` companion container's internal-only HTTP
endpoint to actually trigger a pull + migrate + recreate cycle — see
updater/server.py. This module never touches the Docker socket itself;
that privilege is isolated to the updater container alone."""

import httpx

from app.config import settings


class UpdaterUnavailableError(Exception):
    pass


async def trigger_update(version: str) -> None:
    """`version` (e.g. "v0.1.2-rc4") is the specific tag to pull — the
    updater has no other way to know which release the admin console
    resolved as "latest", since a static APP_VERSION in .env only ever
    covers one fixed value (usually unset, i.e. :latest). See
    updater/server.py's _run_update for how this gets applied."""
    if not settings.updater_url or not settings.updater_shared_secret:
        raise UpdaterUnavailableError("updater isn't configured (UPDATER_URL/UPDATER_SHARED_SECRET unset)")

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{settings.updater_url}/trigger",
            headers={"X-Updater-Token": settings.updater_shared_secret},
            json={"version": version},
        )
        resp.raise_for_status()
