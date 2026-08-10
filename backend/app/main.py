from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.db.rls import set_org_context
from app.db.session import async_session_factory
from app.models.organization import Organization
from app.routers import (
    action_queue,
    admin_updates,
    auth,
    dmarc_reports,
    dns_checks,
    domains,
    mailbox_connections,
    onboarding,
    organizations,
    platform_admin,
    selectors,
    sign_in_events,
    users,
)
from app.services.auth import session_manager

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="YetAnotherDmarcTool API")

# CSRF defense: cookies are SameSite=Lax (blocks cross-site *form* submits,
# but not e.g. a cross-site <script> doing a same-site-adjacent GET-triggered
# nav). State-changing requests additionally require this custom header,
# which only same-origin `fetch()`/XHR can set — a bare cross-site form POST
# cannot. The OAuth callback is a real cross-site GET navigation from
# Microsoft and is exempted (GETs are excluded below anyway; it does nothing
# state-changing on its own request line besides setting the session cookie
# it just issued, which is the intended, unauthenticated-by-design step of
# the login flow itself).
_CSRF_HEADER = "X-Requested-With"
_CSRF_HEADER_VALUE = "yetanotherdmarctool"
_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


@app.middleware("http")
async def enforce_csrf_header(request: Request, call_next):
    if (
        request.method in _UNSAFE_METHODS
        and request.url.path.startswith("/api/")
        and request.headers.get(_CSRF_HEADER) != _CSRF_HEADER_VALUE
    ):
        return JSONResponse({"detail": "missing or invalid X-Requested-With header"}, status_code=403)
    return await call_next(request)


@app.middleware("http")
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
        request.method in _UNSAFE_METHODS
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


@app.middleware("http")
async def restrict_mta_sts_hostname(request: Request, call_next):
    """If this instance serves its own MTA-STS policy (mta_sts_policy_* in
    config.py), that hostname must serve *only* that one file — a reverse
    proxy pointed at this same app for mta-sts.<domain> would otherwise
    also expose the full dashboard/login page and API there too, since
    nothing else in this app routes by Host header. 404s everything else
    on that exact Host rather than falling through to the SPA/API."""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if (
        settings.mta_sts_policy_hostname
        and host == settings.mta_sts_policy_hostname
        and request.url.path != "/.well-known/mta-sts.txt"
    ):
        # no-store: browsers/CDNs heuristically caching a *blocked* response
        # (or, before this middleware existed, the real SPA/login page that
        # used to be here) is exactly what made this flaky to diagnose live —
        # this hostname's responses should never be cached, blocked or not.
        return JSONResponse(
            {"detail": "not found"}, status_code=status.HTTP_404_NOT_FOUND, headers={"Cache-Control": "no-store"}
        )
    return await call_next(request)


api_router = APIRouter(prefix="/api")


@api_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment, "version": settings.app_version}


api_router.include_router(auth.router)
api_router.include_router(platform_admin.router)
api_router.include_router(organizations.router)
api_router.include_router(domains.router)
api_router.include_router(users.router)
api_router.include_router(mailbox_connections.router)
api_router.include_router(dmarc_reports.router)
api_router.include_router(selectors.router)
api_router.include_router(dns_checks.router)
api_router.include_router(action_queue.router)
api_router.include_router(onboarding.router)
api_router.include_router(sign_in_events.router)
api_router.include_router(admin_updates.router)

app.include_router(api_router)


@app.get("/.well-known/security.txt")
async def security_txt() -> PlainTextResponse:
    """RFC 9116. 404s entirely if security_contact_email isn't configured —
    the RFC requires at least one Contact field, so there's nothing correct
    to serve without it (see the setting's own docstring in config.py).
    Expires is computed fresh each request (now + 1 year) rather than a
    hardcoded date, so this never silently goes stale from being forgotten."""
    if not settings.security_contact_email:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    expires = (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body = (
        f"Contact: mailto:{settings.security_contact_email}\n"
        f"Expires: {expires}\n"
        "Preferred-Languages: en\n"
        f"Canonical: {settings.public_base_url}/.well-known/security.txt\n"
    )
    return PlainTextResponse(body, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"})


@app.get("/.well-known/mta-sts.txt")
async def mta_sts_policy(request: Request) -> PlainTextResponse:
    """This instance's own MTA-STS policy (see mta_sts_policy_* in
    config.py) — 404s unless both are configured AND the request's Host
    header matches exactly, since this same app also answers on its own
    dashboard hostname, which isn't what this policy is for."""
    host = (request.headers.get("host") or "").split(":")[0].lower()
    if not settings.mta_sts_policy_hostname or not settings.mta_sts_policy_body or host != settings.mta_sts_policy_hostname:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    return PlainTextResponse(
        settings.mta_sts_policy_body, media_type="text/plain; charset=utf-8", headers={"Cache-Control": "no-store"}
    )


# Serve the built SPA's static assets (JS/CSS/etc.) if present. In Phase 0 the
# image always contains a build (see backend/Dockerfile); this guard just keeps
# `uvicorn app.main:app --reload` usable when running the backend outside Docker
# without having run `npm run build` locally.
if STATIC_DIR.exists():
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="spa-assets")

    # no-store on both branches: the JS/CSS under /assets/ (mounted above)
    # are content-hashed per build and fine to cache hard, but everything
    # served through here — index.html, LICENSE, robots.txt — isn't, and
    # Starlette's FileResponse sets no Cache-Control by default, leaving
    # browsers to apply their own heuristic caching. That's exactly what
    # made the mta-sts hostname fix look "flaky" live: a browser that had
    # cached the SPA shell from before restrict_mta_sts_hostname existed
    # kept serving it back across refreshes, even though the server was
    # already answering consistently.
    _NO_STORE = {"Cache-Control": "no-store"}

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str) -> FileResponse:
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate, headers=_NO_STORE)
        return FileResponse(STATIC_DIR / "index.html", headers=_NO_STORE)
