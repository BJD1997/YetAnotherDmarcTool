from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User


async def list_users_for_org(db: AsyncSession, organization_id: UUID) -> Sequence[User]:
    result = await db.execute(select(User).where(User.organization_id == organization_id).order_by(User.email))
    return result.scalars().all()


async def get_user_in_org(db: AsyncSession, user_id: UUID, organization_id: UUID) -> User | None:
    user = await db.get(User, user_id)
    if user is None or user.organization_id != organization_id:
        return None
    return user


async def get_user_by_org_and_email(db: AsyncSession, organization_id: UUID, email: str) -> User | None:
    """Exact, case-sensitive match within one org — contrast with
    auth.py's get_local_user_by_email, which is case-insensitive and
    cross-org but scoped to local-auth users only."""
    result = await db.execute(select(User).where(User.organization_id == organization_id, User.email == email))
    return result.scalar_one_or_none()
