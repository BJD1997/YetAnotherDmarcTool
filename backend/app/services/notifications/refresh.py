"""The notifications_refresh worker job: creates a notification for each
new domain or DKIM selector the reports turn up, and resolves each one once
it's handled. Every NOTIFICATIONS_REFRESH_INTERVAL_SECONDS, all
organizations, under the platform-admin RLS bypass like the DNS sweep."""

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_platform_admin_context
from app.db.session import async_session_factory
from app.models.enums import OrganizationStatus, SenderReviewStatus
from app.models.organization import Organization
from app.models.sender_review import SenderReview
from app.repositories import dmarc_reports as dmarc_reports_repo
from app.repositories.notifications import insert_if_new, open_notifications, resolve
from app.repositories.selectors import known_selector_names_for_org
from app.services.jobs.advisory_lock import try_advisory_lock
from app.services.notifications.sources import detected_domain_items, new_selectors

logger = logging.getLogger(__name__)

NOTIFICATIONS_REFRESH_INTERVAL_SECONDS = 600
_NOTIFICATIONS_LOCK_KEY = 0x4E4F5446  # "NOTF"

DOMAIN_DETECTED = "domain_detected"
DKIM_SELECTOR_DETECTED = "dkim_selector_detected"


async def _blocked_senders(db: AsyncSession, organization_id: uuid.UUID) -> set[tuple[uuid.UUID, str]]:
    rows = await db.execute(
        select(SenderReview.domain_id, SenderReview.service_label).where(
            SenderReview.organization_id == organization_id, SenderReview.status == SenderReviewStatus.blocked
        )
    )
    return {(domain_id, label) for domain_id, label in rows.all()}


async def refresh_org_notifications(db: AsyncSession, organization_id: uuid.UUID) -> tuple[int, int]:
    """(created, resolved) for one organization."""
    blocked = await _blocked_senders(db, organization_id)
    created = 0

    for item in await detected_domain_items(db, organization_id):
        if await insert_if_new(
            db, organization_id, DOMAIN_DETECTED, item["name"], None,
            {"message_volume": item["message_volume"], "relationship": item["relationship"]},
        ):
            created += 1

    for finding in await new_selectors(db, organization_id):
        if (finding.domain_id, finding.sender_label) in blocked:
            continue  # a rejected sender's selector isn't worth adding
        if await insert_if_new(
            db, organization_id, DKIM_SELECTOR_DETECTED, f"{finding.domain_id}:{finding.selector}", finding.domain_id,
            {
                "domain_name": finding.domain_name,
                "selector": finding.selector,
                "message_volume": finding.message_volume,
                "sender_label": finding.sender_label,
            },
        ):
            created += 1

    registered = set((await dmarc_reports_repo.registered_domains_by_name(db, organization_id)).keys())
    dismissed = await dmarc_reports_repo.dismissed_domain_names(db, organization_id)
    monitored = await known_selector_names_for_org(db, organization_id)
    resolved = 0
    for notification in await open_notifications(db, organization_id):
        if notification.kind == DOMAIN_DETECTED:
            handled = notification.subject_key in registered or notification.subject_key in dismissed
        else:
            payload = notification.payload or {}
            handled = (notification.domain_id, payload.get("selector")) in monitored or (
                notification.domain_id, payload.get("sender_label")
            ) in blocked
        if handled:
            resolve(notification, "handled")
            resolved += 1
    await db.flush()
    return created, resolved


async def run_notifications_refresh() -> None:
    async with try_advisory_lock(_NOTIFICATIONS_LOCK_KEY) as acquired:
        if not acquired:
            logger.info("notifications refresh already running elsewhere — skipping this trigger")
            return
        async with async_session_factory() as db:
            await set_platform_admin_context(db, is_admin=True)
            org_ids = (
                await db.execute(select(Organization.id).where(Organization.status == OrganizationStatus.active))
            ).scalars().all()
            totals = [0, 0]
            for org_id in org_ids:
                try:
                    created, resolved = await refresh_org_notifications(db, org_id)
                    await db.commit()
                    await set_platform_admin_context(db, is_admin=True)
                    totals[0] += created
                    totals[1] += resolved
                except Exception:
                    logger.exception("notifications refresh failed for organization %s", org_id)
                    await db.rollback()
                    await set_platform_admin_context(db, is_admin=True)
        if any(totals):
            logger.info("notifications refresh: %d created, %d resolved", *totals)
