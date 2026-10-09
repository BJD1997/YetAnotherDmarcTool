"""Refreshes every verified, active domain's stored trend (domain_trends)
— the trend_refresh worker job, every TREND_REFRESH_INTERVAL_SECONDS.
Cross-org, under the platform-admin RLS bypass like the DNS check sweep."""

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_platform_admin_context
from app.db.session import async_session_factory
from app.models.domain import Domain
from app.models.enums import DomainVerificationStatus
from app.repositories.dmarc_reports import daily_totals_excluding_blocked
from app.repositories.trends import upsert_trend
from app.services.jobs.advisory_lock import try_advisory_lock
from app.services.rating.trend import BASELINE_DAYS, RECENT_DAYS, DayCounts, compute_trend

logger = logging.getLogger(__name__)

TREND_REFRESH_INTERVAL_SECONDS = 6 * 3600
_TREND_REFRESH_LOCK_KEY = 0x54524E44  # "TRND"


async def refresh_trends(db: AsyncSession, today: date | None = None) -> int:
    today = today or datetime.now(timezone.utc).date()
    since = datetime.combine(today - timedelta(days=RECENT_DAYS + BASELINE_DAYS), datetime.min.time(), tzinfo=timezone.utc)
    domains = (
        await db.execute(
            select(Domain).where(
                Domain.verification_status == DomainVerificationStatus.verified, Domain.is_active.is_(True)
            )
        )
    ).scalars().all()
    refreshed = 0
    for domain in domains:
        try:
            rows = await daily_totals_excluding_blocked(db, domain.id, since)
            result = compute_trend([DayCounts(day, total, passed) for day, total, passed in rows], today)
            await upsert_trend(db, domain, result)
            refreshed += 1
        except Exception:
            logger.exception("trend refresh failed for domain %s", domain.id)
    return refreshed


async def run_trend_refresh() -> None:
    async with try_advisory_lock(_TREND_REFRESH_LOCK_KEY) as acquired:
        if not acquired:
            logger.info("trend refresh already running elsewhere — skipping this trigger")
            return
        async with async_session_factory() as db:
            await set_platform_admin_context(db, is_admin=True)
            refreshed = await refresh_trends(db)
            await db.commit()
        logger.info("trend refresh: %d domain(s)", refreshed)
