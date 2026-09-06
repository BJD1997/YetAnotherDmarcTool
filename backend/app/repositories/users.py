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
