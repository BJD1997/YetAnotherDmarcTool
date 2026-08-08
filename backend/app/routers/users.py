import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.enums import AuthMethod, UserRole, UserStatus
from app.models.organization import Organization
from app.models.password_setup_token import PasswordSetupToken
from app.models.user import User
from app.services.auth.tokens import new_opaque_token

router = APIRouter(prefix="/users", tags=["users"])


class UserUpdateRequest(BaseModel):
    role: UserRole | None = None
    status: UserStatus | None = None


class LocalUserCreateRequest(BaseModel):
    email: EmailStr
    display_name: str | None = None
    role: UserRole = UserRole.member


def _user_out(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role.value,
        "status": user.status.value,
        "auth_method": user.auth_method.value,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


@router.get("")
async def list_users(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    result = await db.execute(
        select(User).where(User.organization_id == user.organization_id).order_by(User.email)
    )
    return [_user_out(u) for u in result.scalars().all()]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_local_user(
    body: LocalUserCreateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_org_admin),
) -> dict:
    """Lets a local-auth org's own admin add teammates the same way a
    platform admin bootstraps that org's first user (see
    platform_admin.create_local_user) — gives local-auth orgs the same
    "admin shares a link, no operator involvement per teammate" parity
    Entra orgs already have via Team.tsx's ShareSignInLink."""
    org = await db.get(Organization, admin.organization_id)
    if org is None or org.entra_tenant_id is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "this organization uses Entra SSO — teammates join by signing in, not manual creation"
        )

    new_user = User(
        organization_id=admin.organization_id,
        auth_method=AuthMethod.local,
        email=body.email,
        display_name=body.display_name,
        role=body.role,
    )
    db.add(new_user)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "a user with this email already exists")

    raw_token, token_hash = new_opaque_token()
    now = datetime.now(timezone.utc)
    db.add(
        PasswordSetupToken(
            user_id=new_user.id,
            token_hash=token_hash,
            created_at=now,
            expires_at=now + timedelta(hours=settings.password_setup_token_timeout_hours),
        )
    )
    # refresh() must run before commit() — users is RLS-protected, and
    # commit ends the SET LOCAL app.current_org_id context this transaction
    # needs for the refresh's SELECT to see the row at all (see
    # app/db/rls.py's own docstring on this exact gotcha).
    await db.flush()
    await db.refresh(new_user)
    await db.commit()

    return {**_user_out(new_user), "setup_link": f"{settings.public_base_url}/set-password?token={raw_token}"}


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
