import uuid
from collections.abc import Sequence
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification import Notification

RECENTLY_RESOLVED_LIMIT = 20


async def insert_if_new(
    db: AsyncSession, organization_id: uuid.UUID, kind: str, subject_key: str, domain_id: uuid.UUID | None, payload: dict
) -> bool:
    """True if this created a notification; an existing one (open or
    resolved) for the same org, kind and subject is left alone."""
    stmt = (
        pg_insert(Notification)
        .values(
            id=uuid.uuid4(), organization_id=organization_id, kind=kind, subject_key=subject_key,
            domain_id=domain_id, payload=payload,
        )
        .on_conflict_do_nothing(constraint="uq_notifications_org_kind_subject")
        .returning(Notification.id)
    )
    return (await db.execute(stmt)).first() is not None


async def open_notifications(db: AsyncSession, organization_id: uuid.UUID) -> Sequence[Notification]:
    result = await db.execute(
        select(Notification)
        .where(Notification.organization_id == organization_id, Notification.resolved_at.is_(None))
        .order_by(Notification.created_at.desc())
    )
    return result.scalars().all()


async def recently_resolved(db: AsyncSession, organization_id: uuid.UUID) -> Sequence[Notification]:
    result = await db.execute(
        select(Notification)
        .where(Notification.organization_id == organization_id, Notification.resolved_at.is_not(None))
        .order_by(Notification.resolved_at.desc())
        .limit(RECENTLY_RESOLVED_LIMIT)
    )
    return result.scalars().all()


async def get_notification(db: AsyncSession, notification_id: uuid.UUID, organization_id: uuid.UUID) -> Notification | None:
    result = await db.execute(
        select(Notification).where(Notification.id == notification_id, Notification.organization_id == organization_id)
    )
    return result.scalar_one_or_none()


def resolve(notification: Notification, reason: str, user_id: uuid.UUID | None = None) -> None:
    notification.resolved_at = datetime.now(timezone.utc)
    notification.resolved_reason = reason
    notification.resolved_by = user_id
