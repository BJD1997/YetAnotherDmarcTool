import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.enums import UserRole, UserStatus
from app.models.user import User

router = APIRouter(prefix="/users", tags=["users"])


class UserUpdateRequest(BaseModel):
    role: UserRole | None = None
    status: UserStatus | None = None


def _user_out(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role.value,
        "status": user.status.value,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


@router.get("")
async def list_users(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    result = await db.execute(
        select(User).where(User.organization_id == user.organization_id).order_by(User.email)
    )
    return [_user_out(u) for u in result.scalars().all()]


@router.patch("/{user_id}")
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_org_admin),
) -> dict:
    target = await db.get(User, user_id)
    if target is None or target.organization_id != admin.organization_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    if target.id == admin.id and body.role is not None and body.role != UserRole.org_admin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot demote yourself")

    if body.role is not None:
        target.role = body.role
    if body.status is not None:
        target.status = body.status

    await db.flush()
    await db.refresh(target)
    await db.commit()
    return _user_out(target)
