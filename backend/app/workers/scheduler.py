"""Entrypoint for the `worker` container. A single AsyncIOScheduler process
— no distributed locking needed since there's only ever one worker replica
(see the plan's rationale for skipping Celery/Redis at this scale).

Registers one interval job per organization with a granted mailbox
connection, plus a reconciliation sweep that adds/removes jobs as orgs get
onboarded/offboarded without needing a worker restart.
"""

import asyncio
import logging
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.db.rls import set_platform_admin_context
from app.db.session import async_session_factory
from app.models.enums import ConsentStatus
from app.models.mailbox_connection import MailboxConnection
from app.models.organization import Organization
from app.services.dns_checks.domain_verification import run_domain_verification_sweep
from app.services.dns_checks.scheduled_recheck import DNS_CHECK_SWEEP_TICK_SECONDS, run_dns_check_sweep
from app.services.retention.forensic_purge import run_retention_purge
from app.services.update_check import run_update_check
from app.workers.jobs.hosted_reports_poll_job import poll_hosted_reports_mailbox
from app.workers.jobs.mailbox_poll_job import poll_org_mailbox

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")

MAILBOX_POLL_INTERVAL_SECONDS = 600
RECONCILE_INTERVAL_SECONDS = 300
RETENTION_PURGE_INTERVAL_SECONDS = 24 * 3600
DOMAIN_VERIFICATION_SWEEP_INTERVAL_SECONDS = 300
HOSTED_REPORTS_POLL_INTERVAL_SECONDS = 600
UPDATE_CHECK_INTERVAL_SECONDS = 6 * 3600
_JOB_PREFIX = "mailbox_poll:"

scheduler = AsyncIOScheduler()


async def _list_pollable_orgs() -> list:
    """Cross-org by design (the worker isn't acting on behalf of any one
    tenant) — uses the is_platform_admin RLS bypass rather than a per-org
    context, same mechanism the platform-admin API routes use."""
    async with async_session_factory() as db:
        await set_platform_admin_context(db, is_admin=True)
        result = await db.execute(
            select(Organization.id, Organization.entra_tenant_id)
            .join(MailboxConnection, MailboxConnection.organization_id == Organization.id)
            .where(
                MailboxConnection.consent_status == ConsentStatus.granted,
                Organization.entra_tenant_id.is_not(None),
            )
        )
        return result.all()


async def _reconcile_jobs() -> None:
    orgs = await _list_pollable_orgs()
    current_job_ids = {job.id for job in scheduler.get_jobs() if job.id.startswith(_JOB_PREFIX)}
    desired_job_ids = set()

    for org_id, tenant_id in orgs:
        job_id = f"{_JOB_PREFIX}{org_id}"
        desired_job_ids.add(job_id)
        if job_id not in current_job_ids:
            scheduler.add_job(
                poll_org_mailbox,
                trigger="interval",
                seconds=MAILBOX_POLL_INTERVAL_SECONDS,
                id=job_id,
                kwargs={"organization_id": org_id, "tenant_id": str(tenant_id)},
                next_run_time=datetime.now(timezone.utc),  # don't make a freshly-onboarded org wait a full interval
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            logger.info("registered mailbox poll job for org %s", org_id)

    for job_id in current_job_ids - desired_job_ids:
        scheduler.remove_job(job_id)
        logger.info("removed mailbox poll job %s (org no longer eligible)", job_id)


async def main() -> None:
    await _reconcile_jobs()
    scheduler.add_job(
        _reconcile_jobs,
        trigger="interval",
        seconds=RECONCILE_INTERVAL_SECONDS,
        id="reconcile",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_retention_purge,
        trigger="interval",
        seconds=RETENTION_PURGE_INTERVAL_SECONDS,
        id="forensic_retention_purge",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_domain_verification_sweep,
        trigger="interval",
        seconds=DOMAIN_VERIFICATION_SWEEP_INTERVAL_SECONDS,
        id="domain_verification_sweep",
        next_run_time=datetime.now(timezone.utc),  # someone may be actively waiting on this in onboarding
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_dns_check_sweep,
        trigger="interval",
        seconds=DNS_CHECK_SWEEP_TICK_SECONDS,
        id="dns_check_sweep",
        next_run_time=datetime.now(timezone.utc),  # verified domains with no check yet shouldn't wait a full tick
        max_instances=1,
        coalesce=True,
    )
    # One shared mailbox, not per-org — unlike poll_org_mailbox above, this
    # is a single flat job (see hosted_reports_poll_job's own docstring). A
    # no-op every run until HOSTED_REPORTS_TENANT_ID/MAILBOX_ADDRESS are
    # actually configured.
    scheduler.add_job(
        poll_hosted_reports_mailbox,
        trigger="interval",
        seconds=HOSTED_REPORTS_POLL_INTERVAL_SECONDS,
        id="hosted_reports_poll",
        max_instances=1,
        coalesce=True,
    )
    # A no-op every run if settings.update_check_enabled is False.
    scheduler.add_job(
        run_update_check,
        trigger="interval",
        seconds=UPDATE_CHECK_INTERVAL_SECONDS,
        id="update_check",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    logger.info(
        "scheduler started (mailbox poll every %ss, reconcile every %ss, retention purge every %ss, "
        "domain verification sweep every %ss, dns check sweep every %ss, hosted reports poll every %ss, "
        "update check every %ss)",
        MAILBOX_POLL_INTERVAL_SECONDS, RECONCILE_INTERVAL_SECONDS, RETENTION_PURGE_INTERVAL_SECONDS,
        DOMAIN_VERIFICATION_SWEEP_INTERVAL_SECONDS, DNS_CHECK_SWEEP_TICK_SECONDS, HOSTED_REPORTS_POLL_INTERVAL_SECONDS,
        UPDATE_CHECK_INTERVAL_SECONDS,
    )
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
