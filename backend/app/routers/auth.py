import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.rls import set_org_context
from app.db.session import get_db
from app.middleware.tenant_context import get_current_user
from app.models.enums import OrganizationStatus, UserRole
from app.models.organization import Organization
from app.models.user import User
from app.services.auth import entra_oidc, pkce, session_manager
from app.services.auth.session_manager import cookie_kwargs

router = APIRouter(prefix="/auth", tags=["auth"])

_OAUTH_STATE_COOKIE = "oauth_state"
_OAUTH_VERIFIER_COOKIE = "oauth_verifier"


@router.get("/login")
async def login() -> RedirectResponse:
    state = secrets.token_urlsafe(24)
    verifier = pkce.generate_verifier()
    challenge = pkce.derive_challenge(verifier)

    auth_url = entra_oidc.build_authorization_url(
        state=state, code_challenge=challenge, redirect_uri=settings.entra_sso_redirect_uri
    )
    response = RedirectResponse(auth_url, status_code=302)
    short_lived = {**cookie_kwargs(), "max_age": 600}
    response.set_cookie(_OAUTH_STATE_COOKIE, state, **short_lived)
    response.set_cookie(_OAUTH_VERIFIER_COOKIE, verifier, **short_lived)
    return response


@router.get("/callback")
async def callback(request: Request, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    if request.query_params.get("error"):
        return RedirectResponse(f"/login?error={request.query_params['error']}", status_code=302)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    cookie_state = request.cookies.get(_OAUTH_STATE_COOKIE)
    verifier = request.cookies.get(_OAUTH_VERIFIER_COOKIE)

    if not code or not state or not cookie_state or not secrets.compare_digest(state, cookie_state) or not verifier:
        return RedirectResponse("/login?error=invalid_state", status_code=302)

    try:
        tokens = await entra_oidc.exchange_code_for_tokens(
            code=code, code_verifier=verifier, redirect_uri=settings.entra_sso_redirect_uri
        )
        claims = await entra_oidc.validate_id_token(tokens["id_token"])
    except (entra_oidc.TokenValidationError, KeyError):
        return RedirectResponse("/login?error=token_invalid", status_code=302)

    tenant_id = claims["tid"]
    object_id = claims["oid"]
    email = claims.get("preferred_username") or claims.get("email") or ""
    display_name = claims.get("name")

    result = await db.execute(select(Organization).where(Organization.entra_tenant_id == tenant_id))
    org = result.scalar_one_or_none()
    if org is None or org.status != OrganizationStatus.active:
        # Deliberate: orgs are provisioned by a platform admin ahead of time,
        # never auto-created just because some Entra tenant signed in.
        return RedirectResponse("/login?error=organization_not_provisioned", status_code=302)

    await set_org_context(db, org.id)

    result = await db.execute(
        select(User).where(User.organization_id == org.id, User.entra_object_id == object_id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        count_result = await db.execute(
            select(func.count()).select_from(User).where(User.organization_id == org.id)
        )
        is_first_user = count_result.scalar_one() == 0
        user = User(
            organization_id=org.id,
            entra_object_id=object_id,
            email=email,
            display_name=display_name,
            role=UserRole.org_admin if is_first_user else UserRole.member,
        )
        db.add(user)
        await db.flush()
    else:
        user.email = email
        user.display_name = display_name

    user.last_login_at = datetime.now(timezone.utc)

    _, raw_token = await session_manager.create_user_session(
        db,
        user_id=user.id,
        organization_id=org.id,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    await db.commit()

    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(_OAUTH_STATE_COOKIE, path="/")
    response.delete_cookie(_OAUTH_VERIFIER_COOKIE, path="/")
    response.set_cookie(
        settings.session_cookie_name,
        raw_token,
        max_age=settings.session_idle_timeout_hours * 3600,
        **cookie_kwargs(),
    )
    return response


@router.post("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        session = await session_manager.get_active_user_session(db, raw_token)
        if session is not None:
            session_manager.revoke_session(session)
            await db.commit()
    response = Response(status_code=204)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response


@router.get("/me")
async def me(user: User = Depends(get_current_user)) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role.value,
        "organization_id": str(user.organization_id),
    }
