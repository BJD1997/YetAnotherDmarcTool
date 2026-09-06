from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import JobType
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
