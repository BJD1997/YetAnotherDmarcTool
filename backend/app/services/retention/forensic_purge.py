"""RUF (forensic report) raw-message retention — nulls out
dmarc_forensic_reports.raw_message (and the other fields that can carry
fragments of an actual failing message: authentication_results is just
headers/results text, not message content, so it's left alone) once a row
is older than RETENTION_DAYS. Structured metadata (source IP, dates,
reported_domain) is kept indefinitely for analytics — this is purely about
bounding how long potentially-PII-bearing message content sticks around,
per the confirmed compliance decision (see the plan).

Runs cross-org in one sweep (the is_platform_admin RLS bypass, same
mechanism the scheduler's own org-listing query uses) since this is a pure
background maintenance task with no single-org request context to scope it
to."""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_platform_admin_context
from app.db.session import async_session_factory
from app.models.dmarc_forensic import DmarcForensicReport

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30


async def purge_old_forensic_raw_messages(db: AsyncSession) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    result = await db.execute(
        DmarcForensicReport.__table__.update()
        .where(DmarcForensicReport.raw_message.is_not(None), DmarcForensicReport.created_at < cutoff)
        .values(raw_message=None)
    )
    return result.rowcount


async def run_retention_purge() -> None:
    async with async_session_factory() as db:
        await set_platform_admin_context(db, is_admin=True)
        purged = await purge_old_forensic_raw_messages(db)
        await db.commit()
        if purged:
            logger.info("purged raw_message from %d forensic report(s) older than %d days", purged, RETENTION_DAYS)
