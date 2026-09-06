from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dmarc_aggregate import DmarcAggregateReport
from app.models.domain import Domain
from app.models.enums import JobStatus, JobType
from app.models.job_run import JobRun
from app.models.organization import Organization
from app.models.platform_admin import PlatformAdmin
from app.models.platform_admin_mfa_pending_challenge import PlatformAdminMfaPendingChallenge
from app.models.platform_admin_recovery_code import PlatformAdminRecoveryCode

JOB_ERROR_WINDOW_DAYS = 7


async def get_platform_admin_by_email(db: AsyncSession, email: str) -> PlatformAdmin | None:
    result = await db.execute(select(PlatformAdmin).where(PlatformAdmin.email == email))
    return result.scalar_one_or_none()


async def get_admin_mfa_pending_challenge(db: AsyncSession, token_hash: str) -> PlatformAdminMfaPendingChallenge | None:
    result = await db.execute(
        select(PlatformAdminMfaPendingChallenge).where(PlatformAdminMfaPendingChallenge.token_hash == token_hash)
    )
    return result.scalar_one_or_none()


async def get_unused_admin_recovery_code(
    db: AsyncSession, platform_admin_id: UUID, code_hash: str
) -> PlatformAdminRecoveryCode | None:
    result = await db.execute(
        select(PlatformAdminRecoveryCode).where(
            PlatformAdminRecoveryCode.platform_admin_id == platform_admin_id,
            PlatformAdminRecoveryCode.code_hash == code_hash,
            PlatformAdminRecoveryCode.used_at.is_(None),
        )
    )
    return result.scalar_one_or_none()


async def list_all_organizations(db: AsyncSession) -> Sequence[Organization]:
    result = await db.execute(select(Organization).order_by(Organization.created_at.desc()))
    return result.scalars().all()


async def org_aggregates(db: AsyncSession, org_ids: list[UUID]) -> dict[UUID, dict]:
    """Batched per-org rollups for the admin organizations list — one GROUP
    BY query per metric across every org at once, not N queries per org.
    RLS is bypassed here the same way it is everywhere else in this router:
    get_current_platform_admin already set app.is_platform_admin=true on
    this transaction, which is what lets a query with no organization_id
    filter of its own see rows across every tenant."""
    if not org_ids:
        return {}

    domain_counts = dict(
        (
            await db.execute(
                select(Domain.organization_id, func.count())
                .where(Domain.organization_id.in_(org_ids))
                .group_by(Domain.organization_id)
            )
        ).all()
    )

    error_cutoff = datetime.now(timezone.utc) - timedelta(days=JOB_ERROR_WINDOW_DAYS)
    job_error_counts = dict(
        (
            await db.execute(
                select(JobRun.organization_id, func.count())
                .where(
                    JobRun.organization_id.in_(org_ids),
                    JobRun.status == JobStatus.failure,
                    JobRun.started_at >= error_cutoff,
                )
                .group_by(JobRun.organization_id)
            )
        ).all()
    )

    last_report_ats = dict(
        (
            await db.execute(
                select(DmarcAggregateReport.organization_id, func.max(DmarcAggregateReport.received_at))
                .where(DmarcAggregateReport.organization_id.in_(org_ids))
                .group_by(DmarcAggregateReport.organization_id)
            )
        ).all()
    )

    return {
        org_id: {
            "domain_count": domain_counts.get(org_id, 0),
            "job_error_count_7d": job_error_counts.get(org_id, 0),
            "last_report_at": (last_report_ats[org_id].isoformat() if last_report_ats.get(org_id) else None),
        }
        for org_id in org_ids
    }


async def list_job_runs(
    db: AsyncSession,
    *,
    limit: int,
    organization_id: UUID | None,
    job_type: JobType | None,
    status_filter: JobStatus | None,
    since_days: int | None,
) -> Sequence[JobRun]:
    query = select(JobRun).order_by(JobRun.started_at.desc())
    if organization_id is not None:
        query = query.where(JobRun.organization_id == organization_id)
    if job_type is not None:
        query = query.where(JobRun.job_type == job_type)
    if status_filter is not None:
        query = query.where(JobRun.status == status_filter)
    if since_days is not None:
        query = query.where(JobRun.started_at >= datetime.now(timezone.utc) - timedelta(days=since_days))
    query = query.limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


async def job_runs_summary_stats(db: AsyncSession) -> dict:
    """Bundles the /job-runs/summary dashboard-card stats in one call,
    matching this codebase's existing pattern of bundling multi-stat
    dashboard endpoints (see dmarc_reports.py's dmarc_posture) rather than
    one repository function per stat. Returns raw ORM objects/numbers —
    the router still shapes the JSON response."""
    last_failure = (
        await db.execute(
            select(JobRun).where(JobRun.status == JobStatus.failure).order_by(JobRun.started_at.desc()).limit(1)
        )
    ).scalar_one_or_none()

    since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
    total_24h, success_24h = (
        await db.execute(
            select(
                func.count(),
                func.coalesce(func.sum(case((JobRun.status == JobStatus.success, 1), else_=0)), 0),
            ).where(JobRun.started_at >= since_24h)
        )
    ).one()

    latest_mailbox_poll_at = (
        await db.execute(select(func.max(JobRun.started_at)).where(JobRun.job_type == JobType.mailbox_poll))
    ).scalar_one_or_none()

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    reports_today = (
        await db.execute(
            select(func.count()).select_from(DmarcAggregateReport).where(DmarcAggregateReport.received_at >= today_start)
        )
    ).scalar_one()

    return {
        "last_failure": last_failure,
        "total_24h": total_24h,
        "success_24h": success_24h,
        "latest_mailbox_poll_at": latest_mailbox_poll_at,
        "reports_today": reports_today,
    }
