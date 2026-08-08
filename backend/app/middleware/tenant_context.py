import uuid
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.rls import set_org_context, set_platform_admin_context
from app.db.session import get_db
from app.models.enums import OrganizationStatus, UserRole, UserStatus
from app.models.organization import Organization
from app.models.platform_admin import PlatformAdmin
from app.models.user import User
from app.services.auth import session_manager


async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    """Resolves the session cookie to a User AND, as a side effect, sets the
    RLS `app.current_org_id` session var on the shared per-request `db`
    connection — every RLS-protected query made later in this same request
    (via the same `Depends(get_db)` instance) is scoped to this user's org.

    The organization_id needed to set that context comes from UserSession
    (not RLS-protected — see app/models/session.py), which is what avoids
    the chicken-and-egg problem of querying the RLS-protected `users` table
    before the org context that would let RLS admit its own row is known.
    """
    raw_token = request.cookies.get(settings.session_cookie_name)
    if not raw_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")

    session = await session_manager.get_active_user_session(db, raw_token)
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired or invalid")

    await set_org_context(db, session.organization_id)
    await set_platform_admin_context(db, is_admin=False)

    user = await db.get(User, session.user_id)
    if user is None or user.status != UserStatus.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "user not active")

    org = await db.get(Organization, session.organization_id)
    # org is None shouldn't actually be reachable now that orgs can be
    # deleted: user_sessions.organization_id cascades on delete too, so a
    # session pointing at a deleted org would already be gone by the time
    # get_active_user_session ran above. Kept as a defensive fallback.
    if org is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "organization not found")
    if org.status == OrganizationStatus.suspended:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "organization suspended")

    return user


async def require_org_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != UserRole.org_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "org_admin role required")
    return user


async def get_current_platform_admin_local(request: Request, db: AsyncSession = Depends(get_db)) -> PlatformAdmin:
    """Strict: only the local platform_admin session path (see
    get_current_platform_admin below for the general, dual-path version).
    Used by endpoints inherently tied to the local account itself — e.g.
    changing its password — which wouldn't make sense for an operator-org
    admin authorizing via their Entra SSO session instead, since there's no
    local password to change in that case."""
    raw_token = request.cookies.get(settings.platform_admin_session_cookie_name)
    if not raw_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")

    session = await session_manager.get_active_platform_admin_session(db, raw_token)
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session expired or invalid")

    await set_platform_admin_context(db, is_admin=True)

    admin = await db.get(PlatformAdmin, session.platform_admin_id)
    if admin is None or not admin.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "admin not active")
    return admin


@dataclass
class AdminPrincipal:
    """Whoever is authorized for the platform-admin API surface, regardless
    of which of the two paths in get_current_platform_admin got them there —
    PlatformAdmin and User are different models with different attribute
    sets, so routes that only need "who is this" (mostly just for the /me
    endpoint) get a small common shape instead of a union type."""

    id: uuid.UUID
    email: str
    auth_type: str  # "local" | "operator_org"


async def get_current_platform_admin(request: Request, db: AsyncSession = Depends(get_db)) -> AdminPrincipal:
    """Authorizes the platform-admin API surface (org provisioning,
    cross-org job-run visibility, etc.) via EITHER of two paths:

    1. A local platform_admin session — the original bootstrap/break-glass
       mechanism (an org has to exist before its users can SSO in, so
       *something* needs a login that doesn't depend on any org existing).
    2. A normal user session where the user is an org_admin of the
       organization flagged is_operator=True — lets the operator manage
       the platform through their own Microsoft-backed SSO login instead
       of a separate local password, once that operator org exists (which,
       after initial bootstrap via path 1, it always does).

    Both paths set the is_platform_admin RLS bypass flag on success, so the
    existing admin routes work unmodified regardless of which path was used.
    """
    admin_token = request.cookies.get(settings.platform_admin_session_cookie_name)
    if admin_token:
        session = await session_manager.get_active_platform_admin_session(db, admin_token)
        if session is not None:
            admin = await db.get(PlatformAdmin, session.platform_admin_id)
            if admin is not None and admin.is_active:
                await set_platform_admin_context(db, is_admin=True)
                return AdminPrincipal(id=admin.id, email=admin.email, auth_type="local")

    user_token = request.cookies.get(settings.session_cookie_name)
    if user_token:
        user_session = await session_manager.get_active_user_session(db, user_token)
        if user_session is not None:
            await set_org_context(db, user_session.organization_id)
            user = await db.get(User, user_session.user_id)
            org = await db.get(Organization, user_session.organization_id)
            if (
                user is not None
                and user.status == UserStatus.active
                and user.role == UserRole.org_admin
                and org is not None
                and org.is_operator
                and org.status == OrganizationStatus.active
            ):
                await set_platform_admin_context(db, is_admin=True)
                return AdminPrincipal(id=user.id, email=user.email, auth_type="operator_org")

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated as platform admin")
