from pathlib import Path

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.routers import (
    auth,
    dmarc_reports,
    dns_checks,
    domains,
    mailbox_connections,
    organizations,
    platform_admin,
    selectors,
    users,
)

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

app = FastAPI(title="DMARC Dashboard API")

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
_CSRF_HEADER_VALUE = "dmarc-dashboard"
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


api_router = APIRouter(prefix="/api")


@api_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


api_router.include_router(auth.router)
api_router.include_router(platform_admin.router)
api_router.include_router(organizations.router)
api_router.include_router(domains.router)
api_router.include_router(users.router)
api_router.include_router(mailbox_connections.router)
api_router.include_router(dmarc_reports.router)
api_router.include_router(selectors.router)
api_router.include_router(dns_checks.router)

app.include_router(api_router)

# Serve the built SPA's static assets (JS/CSS/etc.) if present. In Phase 0 the
# image always contains a build (see backend/Dockerfile); this guard just keeps
# `uvicorn app.main:app --reload` usable when running the backend outside Docker
# without having run `npm run build` locally.
if STATIC_DIR.exists():
    assets_dir = STATIC_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="spa-assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str) -> FileResponse:
        candidate = STATIC_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")
