"""Shared body for running the full best-practice check suite against one
already-verified domain and persisting the results — used by both the
manual "Recheck" button (POST /domains/{id}/checks/recheck) and the
periodic sweep (run_dns_check_sweep, wired up in app/workers/scheduler.py),
so the two can't drift apart. Caller owns the transaction boundary (flush
happens here so a batch of these can share one commit; nothing here calls
commit itself)."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_platform_admin_context
from app.db.session import async_session_factory
from app.models.dkim_selector import DkimSelector
from app.models.dns_check import DnsCheckResult
from app.models.domain import Domain
from app.models.enums import CheckStatus, CheckType, DomainVerificationStatus, JobStatus, JobType
from app.models.job_run import JobRun
from app.models.mailbox_connection import MailboxConnection
from app.models.organization import Organization
from app.services.dns_checks.registry import run_all

logger = logging.getLogger(__name__)

# How stale a domain's last check has to be before the sweep re-runs it.
# Deliberately much coarser than the mailbox-poll interval (10 min): unlike
# new reports, DNS records rarely change, and several of these checks (the
# STARTTLS probe in particular) open a real SMTP connection to the domain's
# own MX hosts — polling every few minutes would mean repeatedly connecting
# to a customer's mail servers for no reason.
DNS_CHECK_STALE_AFTER = timedelta(hours=6)
# How often the sweep itself wakes up to look for due domains — deliberately
# more frequent than the staleness window so a newly-verified domain (which
# has no prior check at all, so is always "due") gets its first check
# promptly rather than waiting up to 6h, mirroring domain_verification_sweep's
# own reasoning for onboarding responsiveness.
DNS_CHECK_SWEEP_TICK_SECONDS = 900


async def run_and_persist_checks(db: AsyncSession, domain: Domain, org: Organization) -> list[DnsCheckResult]:
    selector_result = await db.execute(select(DkimSelector).where(DkimSelector.domain_id == domain.id))
    selectors = selector_result.scalars().all()
    selector_id_by_name = {s.selector: s.id for s in selectors}

    parent_domain_name = None
    if domain.parent_domain_id is not None:
        parent = await db.get(Domain, domain.parent_domain_id)
        if parent is not None:
            parent_domain_name = parent.name

    # Same resolution as the DMARC/TLS-RPT policy builders' own
    # org_mailbox_address (app/routers/dns_checks.py) — a domain's own
    # hosted address takes priority over the org's shared connected
    # mailbox. Passed through to tls_rpt_check.check() so the scored check
    # can flag a rua= that doesn't include it, not just the builder.
    connection = (
        await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == domain.organization_id))
    ).scalar_one_or_none()
    mailbox_address = domain.hosted_report_address or (connection.mailbox_address if connection is not None else None)

    findings_by_type = await run_all(
        domain.name,
        [s.selector for s in selectors],
        domain.mail_profile,
        org.spf_all_qualifier_mode,
        parent_domain_name,
        mailbox_address,
    )

    now = datetime.now(timezone.utc)
    rows = [
        DnsCheckResult(
            organization_id=domain.organization_id,
            domain_id=domain.id,
            check_type=check_type,
            dkim_selector_id=selector_id_by_name.get(finding.subject) if check_type == CheckType.dkim else None,
            subject=finding.subject,
            status=CheckStatus(finding.status),
            summary=finding.summary,
            details=finding.details,
            rule_version=finding.rule_version,
            checked_at=now,
        )
        for check_type, findings in findings_by_type.items()
        for finding in findings
    ]
    db.add_all(rows)
    await db.flush()
    return rows


async def _due_domain_ids(db: AsyncSession, cutoff: datetime) -> list[uuid.UUID]:
    latest_checked = (
        select(DnsCheckResult.domain_id, func.max(DnsCheckResult.checked_at).label("latest"))
        .group_by(DnsCheckResult.domain_id)
        .subquery()
    )
    result = await db.execute(
        select(Domain.id)
        .outerjoin(latest_checked, latest_checked.c.domain_id == Domain.id)
        .where(
            Domain.verification_status == DomainVerificationStatus.verified,
            Domain.is_active.is_(True),
            (latest_checked.c.latest.is_(None)) | (latest_checked.c.latest < cutoff),
        )
    )
    return list(result.scalars().all())


async def run_dns_check_sweep() -> None:
    """Background counterpart to POST /domains/{id}/checks/recheck — runs
    the best-practice check suite for every verified, active domain across
    every org whose latest results are older than DNS_CHECK_STALE_AFTER (or
    that have never been checked at all). Cross-org in one sweep, same
    is_platform_admin bypass run_domain_verification_sweep/
    run_retention_purge use. One domain's checker crashing (e.g. a
    transient DNS timeout) is logged and skipped rather than aborting the
    rest of the sweep.

    Writes one JobRun per tick (job_type=dns_check — see the platform-admin
    "Job runs" page/filter), even on ticks where nothing was due, so that
    view shows the sweep is alive rather than looking dead between
    genuinely-due runs. organization_id/domain_id are left null, same as
    JobRun's own docstring anticipates for this exact sweep — it's a
    cross-org tick, not one org's activity."""
    started_at = datetime.now(timezone.utc)
    checked_count = 0
    failed_count = 0
    error_message: str | None = None

    try:
        async with async_session_factory() as db:
            await set_platform_admin_context(db, is_admin=True)
            cutoff = started_at - DNS_CHECK_STALE_AFTER
            domain_ids = await _due_domain_ids(db, cutoff)

            for domain_id in domain_ids:
                domain = await db.get(Domain, domain_id)
                if domain is None:
                    continue
                org = await db.get(Organization, domain.organization_id)
                if org is None:
                    continue
                try:
                    await run_and_persist_checks(db, domain, org)
                    checked_count += 1
                except Exception:
                    logger.exception("dns check sweep failed for domain %s", domain_id)
                    failed_count += 1

            if checked_count:
                logger.info("dns check sweep: checked %d domain(s)", checked_count)
            await db.commit()
    except Exception as exc:
        logger.exception("dns check sweep crashed")
        error_message = str(exc)[:2000]

    async with async_session_factory() as db:
        await set_platform_admin_context(db, is_admin=True)
        db.add(
            JobRun(
                job_type=JobType.dns_check,
                status=JobStatus.failure if error_message else JobStatus.success,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                error_message=error_message,
                stats={"domains_checked": checked_count, "domains_failed": failed_count},
            )
        )
        await db.commit()
