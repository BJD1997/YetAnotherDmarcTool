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
    organization_name: str | None = None  # operator_org only
    # True when the browser also holds the other kind of admin session, so
    # the console can offer to switch.
    can_switch: bool = False


CHOOSE_SIGN_IN = "choose which sign-in to use for the admin console"


async def _local_admin_candidate(request: Request, db: AsyncSession) -> AdminPrincipal | None:
    admin_token = request.cookies.get(settings.platform_admin_session_cookie_name)
    if not admin_token:
        return None
    session = await session_manager.get_active_platform_admin_session(db, admin_token)
    if session is None:
        return None
    admin = await db.get(PlatformAdmin, session.platform_admin_id)
    if admin is None or not admin.is_active:
        return None
    return AdminPrincipal(id=admin.id, email=admin.email, auth_type="local")


async def _operator_org_candidate(request: Request, db: AsyncSession) -> AdminPrincipal | None:
    user_token = request.cookies.get(settings.session_cookie_name)
    if not user_token:
        return None
    user_session = await session_manager.get_active_user_session(db, user_token)
    if user_session is None:
        return None
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
        return AdminPrincipal(id=user.id, email=user.email, auth_type="operator_org", organization_name=org.name)
    return None


async def admin_sign_in_candidates(request: Request, db: AsyncSession) -> dict[str, AdminPrincipal]:
    """Every admin identity this browser's cookies can act as, keyed by
    auth_type — zero, one, or both."""
    candidates = {}
    for resolve in (_local_admin_candidate, _operator_org_candidate):
        principal = await resolve(request, db)
        if principal is not None:
            candidates[principal.auth_type] = principal
    return candidates


async def get_current_platform_admin(request: Request, db: AsyncSession = Depends(get_db)) -> AdminPrincipal:
    """Authorizes the platform-admin API surface (org provisioning,
    cross-org job-run visibility, etc.) via EITHER of two paths:

    1. A local platform_admin session — the original bootstrap/break-glass
       mechanism (an org has to exist before its users can SSO in, so
       *something* needs a login that doesn't depend on any org existing).
    2. A normal user session where the user is an org_admin of an
       organization flagged is_operator=True — lets the operator manage
       the platform through their own sign-in instead of a separate local
       password.

    A browser can hold both at once (and the two can even share an email
    address). Then the identity isn't guessed: the one named by the
    platform_admin_choice cookie is used (set via POST /admin/session-choice,
    or by a break-glass login itself), and without a valid choice this
    answers 409 CHOOSE_SIGN_IN so the console can ask.

    Either path sets the is_platform_admin RLS bypass flag on success, so the
    existing admin routes work unmodified regardless of which was used.
    """
    candidates = await admin_sign_in_candidates(request, db)
    if not candidates:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated as platform admin")

    if len(candidates) == 1:
        principal = next(iter(candidates.values()))
    else:
        choice = request.cookies.get(settings.platform_admin_choice_cookie_name)
        if choice not in candidates:
            raise HTTPException(status.HTTP_409_CONFLICT, CHOOSE_SIGN_IN)
        principal = candidates[choice]
        principal.can_switch = True

    await set_platform_admin_context(db, is_admin=True)
    return principal
