"""Talks to the `updater` companion container's internal-only HTTP
endpoint to actually trigger a pull + migrate + recreate cycle — see
updater/server.py. This module never touches the Docker socket itself;
that privilege is isolated to the updater container alone."""

import httpx

from app.config import settings


class UpdaterUnavailableError(Exception):
    pass


async def trigger_update() -> None:
    if not settings.updater_url or not settings.updater_shared_secret:
        raise UpdaterUnavailableError("updater isn't configured (UPDATER_URL/UPDATER_SHARED_SECRET unset)")

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(
            f"{settings.updater_url}/trigger",
            headers={"X-Updater-Token": settings.updater_shared_secret},
        )
        resp.raise_for_status()
