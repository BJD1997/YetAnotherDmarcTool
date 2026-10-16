from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import ConsentStatus, JobType, MailboxKind
from app.models.hosted_reports_poll_state import HostedReportsPollState
from app.models.job_run import JobRun
from app.models.mailbox_connection import MailboxConnection


async def get_org_mailbox_connection(db: AsyncSession, organization_id: UUID) -> MailboxConnection | None:
    result = await db.execute(select(MailboxConnection).where(MailboxConnection.organization_id == organization_id))
    return result.scalar_one_or_none()


async def list_mailbox_job_runs(db: AsyncSession, organization_id: UUID, *, limit: int) -> Sequence[JobRun]:
    result = await db.execute(
        select(JobRun)
        .where(JobRun.organization_id == organization_id, JobRun.job_type == JobType.mailbox_poll)
        .order_by(JobRun.started_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


async def list_orgs_with_granted_mailbox_connections(db: AsyncSession) -> Sequence:
    """(organization_id, tenant_id) for every org with a granted mailbox
    connection — the worker's own job-registration list, cross-org by
    design (see app/workers/scheduler.py's _enqueue_mailbox_polls). An IMAP
    mailbox needs no tenant; a Microsoft 365 one uses its own (not the
    organization's sign-in, which may have moved elsewhere)."""
    result = await db.execute(
        select(MailboxConnection.organization_id, MailboxConnection.ms_tenant_id)
        .where(
            MailboxConnection.consent_status == ConsentStatus.granted,
            or_(MailboxConnection.kind == MailboxKind.imap, MailboxConnection.ms_tenant_id.is_not(None)),
        )
    )
    return result.all()


async def get_or_create_hosted_reports_poll_state(db: AsyncSession) -> HostedReportsPollState:
    result = await db.execute(select(HostedReportsPollState).limit(1))
    state = result.scalar_one_or_none()
    if state is None:
        state = HostedReportsPollState()
        db.add(state)
        await db.flush()
    return state
