"""Periodic check against GitHub's releases API for the latest tagged
version — registered on the worker's scheduler (app/workers/scheduler.py).
A no-op if settings.update_check_enabled is False (e.g. an air-gapped
instance with no outbound internet)."""

import logging
import re
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.db.session import async_session_factory
from app.models.update_check_state import UpdateCheckState
from app.repositories.admin_updates import get_or_create_update_check_state

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"

_VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:-(beta|rc)(\d+))?$")

# Prerelease channel ordering for same major.minor.patch: beta < rc < stable.
# Every -rcN outranks every -betaN regardless of number — they're separate
# channels, not one shared counter (see this project's own tag history:
# v0.1.4-beta1, then v0.1.4-rc1..rc6, then v0.1.4 stable).
_CHANNEL_RANK = {"beta": 0, "rc": 1, None: 2}


def _parse_version(version: str) -> tuple[int, int, int, int, int] | None:
    match = _VERSION_RE.match(version)
    if match is None:
        return None
    major, minor, patch, channel, number = match.groups()
    number_rank = int(number) if number is not None else 0
    return (int(major), int(minor), int(patch), _CHANNEL_RANK[channel], number_rank)


def is_newer_version(latest: str, running: str) -> bool:
    """True only if `latest` is a recognized release tag that genuinely
    outranks `running` (e.g. running v0.1.2-rc2 with prereleases switched
    back off must NOT flag the older stable v0.1.1 as an available update).

    A `running` version that isn't a recognized vX.Y.Z[-rcN] tag — most
    importantly a locally-built "dev" image — is treated as off the release
    channel and never has an update "available": it's presumed built from
    source and at least current, and "updating" it would in fact DOWNGRADE it
    to an older tagged release, silently replacing the local build (which is
    what made the updater feel trigger-happy on dev builds, and was a real
    downgrade footgun). A `latest` that doesn't parse likewise gives nothing
    safe to compare against."""
    latest_parsed = _parse_version(latest)
    running_parsed = _parse_version(running)
    if latest_parsed is None or running_parsed is None:
        return False
    return latest_parsed > running_parsed


def is_dev_build(version: str) -> bool:
    """True when `version` isn't a recognized release tag (e.g. a locally
    built "dev" image) — such a build is off the release channel, so the
    updater neither prompts for nor allows an update toward a tagged release."""
    return _parse_version(version) is None


def pick_latest_release(releases: list[dict]) -> dict | None:
    """The highest-versioned release in a GitHub releases listing. GitHub
    doesn't list them newest-first (it sorts by tag name, so v0.2.0-beta9
    came before beta10 and beta11), so the order can't be trusted. Drafts and
    tags that aren't vX.Y.Z[-betaN|-rcN] are skipped."""
    candidates = [
        (parsed, release)
        for release in releases
        if not release.get("draft") and (parsed := _parse_version(release.get("tag_name", ""))) is not None
    ]
    return max(candidates, key=lambda c: c[0])[1] if candidates else None


async def get_or_create_state(db) -> UpdateCheckState:
    """Also used directly by GET /admin/updates to read the cached result
    without re-running the check."""
    return await get_or_create_update_check_state(db)


async def run_update_check() -> None:
    if not settings.update_check_enabled:
        return

    async with async_session_factory() as db:
        state = await get_or_create_state(db)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                if state.include_prereleases:
                    # The list endpoint (not /latest) is the only way to see
                    # prereleases. It isn't sorted by version, so take a full
                    # page and pick the highest ourselves.
                    resp = await client.get(
                        f"{GITHUB_API_BASE}/repos/{settings.update_check_repo}/releases",
                        headers={"Accept": "application/vnd.github+json"},
                        params={"per_page": 100},
                    )
                    resp.raise_for_status()
                    release = pick_latest_release(resp.json())
                    if release is None:
                        raise httpx.HTTPError("no releases found")
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
