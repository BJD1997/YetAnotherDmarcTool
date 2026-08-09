"""Periodic check against GitHub's releases API for the latest tagged
version — registered on the worker's scheduler (app/workers/scheduler.py).
A no-op if settings.update_check_enabled is False (e.g. an air-gapped
instance with no outbound internet)."""

import logging
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.config import settings
from app.db.session import async_session_factory
from app.models.update_check_state import UpdateCheckState

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"

_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-rc(\d+))?$")


def _parse_version(version: str) -> tuple[int, int, int, float] | None:
    match = _VERSION_RE.match(version)
    if match is None:
        return None
    major, minor, patch, rc = match.groups()
    # A stable release outranks every -rcN of the same major.minor.patch —
    # inf sorts higher than any real rc number.
    prerelease_rank = float(rc) if rc is not None else float("inf")
    return (int(major), int(minor), int(patch), prerelease_rank)


def is_newer_version(latest: str, running: str) -> bool:
    """True only if `latest` actually outranks `running` — plain inequality
    would flag a "downgrade" (e.g. running v0.1.2-rc2 with prereleases
    switched back off, so the latest known release becomes the older stable
    v0.1.1) as an available update. Falls back to inequality when either
    side isn't a recognized vX.Y.Z[-rcN] tag (e.g. a locally-built "dev"
    image), since there's nothing more precise to compare against."""
    latest_parsed = _parse_version(latest)
    running_parsed = _parse_version(running)
    if latest_parsed is None or running_parsed is None:
        return latest != running
    return latest_parsed > running_parsed


async def get_or_create_state(db) -> UpdateCheckState:
    """Also used directly by GET /admin/updates to read the cached result
    without re-running the check."""
    result = await db.execute(select(UpdateCheckState).limit(1))
    state = result.scalar_one_or_none()
    if state is None:
        state = UpdateCheckState()
        db.add(state)
        await db.flush()
    return state


async def run_update_check() -> None:
    if not settings.update_check_enabled:
        return

    async with async_session_factory() as db:
        state = await get_or_create_state(db)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                if state.include_prereleases:
                    # The list endpoint (not /latest) is the only way to see
                    # prereleases — it's already newest-first and excludes
                    # drafts with no extra filtering needed, so the first
                    # entry is simply "the latest release, prerelease or
                    # not".
                    resp = await client.get(
                        f"{GITHUB_API_BASE}/repos/{settings.update_check_repo}/releases",
                        headers={"Accept": "application/vnd.github+json"},
                        params={"per_page": 1},
                    )
                    resp.raise_for_status()
                    releases = resp.json()
                    if not releases:
                        raise httpx.HTTPError("no releases found")
                    release = releases[0]
                else:
                    resp = await client.get(
                        f"{GITHUB_API_BASE}/repos/{settings.update_check_repo}/releases/latest",
                        headers={"Accept": "application/vnd.github+json"},
                    )
                    resp.raise_for_status()
                    release = resp.json()

            state.latest_version = release["tag_name"]
            state.latest_release_url = release["html_url"]
            state.latest_release_notes = release.get("body")
            published_at = release.get("published_at")
            state.latest_published_at = (
                datetime.fromisoformat(published_at.replace("Z", "+00:00")) if published_at else None
            )
            state.checked_at = datetime.now(timezone.utc)
            state.check_error = None
        except httpx.HTTPError as exc:
            # Leave the last-known-good latest_version untouched — a
            # transient GitHub API failure shouldn't make an already-known
            # update disappear from the admin console.
            state.checked_at = datetime.now(timezone.utc)
            state.check_error = str(exc)[:2000]
            logger.warning("update check failed: %s", exc)

        await db.commit()
