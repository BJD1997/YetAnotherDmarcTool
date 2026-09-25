import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.enums import AuthMethod, UserRole, UserStatus
from app.models.user import User
from app.repositories.organizations import get_organization
from app.repositories.users import get_user_in_org, list_users_for_org
from app.schemas.users import LocalUserCreateRequest, UserUpdateRequest
from app.services.auth import account_reset, session_manager

router = APIRouter(prefix="/users", tags=["users"])


def _user_out(user: User) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role.value,
        "status": user.status.value,
        "auth_method": user.auth_method.value,
        "mfa_enrolled": user.otp_enrolled_at is not None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


@router.get("")
async def list_users(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> list[dict]:
    users = await list_users_for_org(db, user.organization_id)
    return [_user_out(u) for u in users]


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
    org = await get_organization(db, admin.organization_id)
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

    setup_link = await account_reset.issue_password_setup_link(db, new_user)
    # refresh() must run before commit() — users is RLS-protected, and
    # commit ends the SET LOCAL app.current_org_id context this transaction
    # needs for the refresh's SELECT to see the row at all (see
    # app/db/rls.py's own docstring on this exact gotcha).
    await db.flush()
    await db.refresh(new_user)
    await db.commit()

    return {**_user_out(new_user), "setup_link": setup_link}


@router.patch("/{user_id}")
async def update_user(
    user_id: uuid.UUID,
    body: UserUpdateRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_org_admin),
) -> dict:
    target = await get_user_in_org(db, user_id, admin.organization_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    if target.id == admin.id and body.role is not None and body.role != UserRole.org_admin:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot demote yourself")
    if target.id == admin.id and body.status is not None and body.status != UserStatus.active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "cannot disable yourself")

    if body.role is not None:
        target.role = body.role
    if body.status is not None:
        target.status = body.status

    await db.flush()
    await db.refresh(target)
    await db.commit()
    return _user_out(target)


async def _local_teammate(db: AsyncSession, admin: User, user_id: uuid.UUID) -> User:
    """Resolves the target of an admin credential reset: another local-auth
    user in the admin's own org. Your own credentials go through the
    Account settings instead, which re-check your current password."""
    target = await get_user_in_org(db, user_id, admin.organization_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
    if target.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "use Settings → Account to change your own sign-in")
    if target.auth_method != AuthMethod.local:
        raise HTTPException(status.HTTP_409_CONFLICT, "this user signs in with Microsoft — reset it in Entra instead")
    return target


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_org_admin),
) -> dict:
    """The old password stops working immediately and every session is
    signed out; the user sets a new one through the returned link. Their
    MFA stays as it is."""
    target = await _local_teammate(db, admin, user_id)
    target.password_hash = None
    setup_link = await account_reset.issue_password_setup_link(db, target)
    await account_reset.cancel_pending_logins(db, target)
    await session_manager.revoke_user_sessions(db, target.id)
    await account_reset.log_account_change(db, request, target, "password_reset_by_admin", actor_email=admin.email)
    await db.commit()
    return {"setup_link": setup_link}


@router.post("/{user_id}/reset-mfa", status_code=status.HTTP_204_NO_CONTENT)
async def reset_mfa(
    user_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_org_admin),
) -> None:
    """Removes the user's authenticator and recovery codes and signs them
    out; they enroll a new authenticator at their next sign-in."""
    target = await _local_teammate(db, admin, user_id)
    await account_reset.clear_mfa(db, target)
    await session_manager.revoke_user_sessions(db, target.id)
    await account_reset.log_account_change(db, request, target, "mfa_reset_by_admin", actor_email=admin.email)
    await db.commit()
