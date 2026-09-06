from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.middleware.csrf import enforce_csrf_header
from app.middleware.demo_read_only import enforce_demo_read_only
from app.middleware.mta_sts_routing import restrict_mta_sts_hostname
from app.middleware.security_headers import add_security_headers
from app.middleware.spa_static import make_serve_spa
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

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="YetAnotherDmarcTool API")

app.middleware("http")(add_security_headers)
app.middleware("http")(enforce_csrf_header)
app.middleware("http")(enforce_demo_read_only)
app.middleware("http")(restrict_mta_sts_hostname)

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

    app.get("/{full_path:path}")(make_serve_spa(STATIC_DIR))
