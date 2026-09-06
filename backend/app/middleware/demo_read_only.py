from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.config import settings
from app.db.rls import set_org_context
from app.db.session import async_session_factory
from app.models.organization import Organization
from app.services.auth import session_manager

from .csrf import UNSAFE_METHODS


async def enforce_demo_read_only(request: Request, call_next):
    """Organizations flagged is_demo_read_only (see the Organization model
    — the one intended use is a published public demo login) can't perform
    any state-changing action. /api/auth/ is exempt so a demo visitor can
    still log in/out/enroll TOTP etc.; everything else under /api/ with an
    unsafe method is blocked.

    /api/admin/ is also exempt — platform-admin auth is a completely
    separate realm from the org-scoped session this check keys off of
    (session_cookie_name, not platform_admin_session_cookie_name), so it
    was never meant to be in scope here. Without this, a stray regular
    dmarc_session cookie left over from ever trying the public demo login
    in the same browser blocks even POST /api/admin/login itself, purely
    because that leftover cookie happens to resolve to the read-only demo
    org — confirmed live: the platform admin couldn't log into their own
    demo instance's admin console because of an unrelated cookie.

    Checked here at the middleware level — not only inside get_current_user/
    require_org_admin — as defense in depth: coverage this way doesn't
    depend on every current and future mutating route correctly using
    those dependencies, the same reasoning restrict_mta_sts_hostname above
    is checked at this layer rather than per-route."""
    if (
        request.method in UNSAFE_METHODS
        and request.url.path.startswith("/api/")
        and not request.url.path.startswith("/api/auth/")
        and not request.url.path.startswith("/api/admin/")
    ):
        raw_token = request.cookies.get(settings.session_cookie_name)
        if raw_token:
            async with async_session_factory() as db:
                session = await session_manager.get_active_user_session(db, raw_token)
                if session is not None:
                    await set_org_context(db, session.organization_id)
                    org = await db.get(Organization, session.organization_id)
                    if org is not None and org.is_demo_read_only:
                        return JSONResponse(
                            {"detail": "This is a read-only public demo — changes aren't saved."},
                            status_code=status.HTTP_403_FORBIDDEN,
                        )
    return await call_next(request)
