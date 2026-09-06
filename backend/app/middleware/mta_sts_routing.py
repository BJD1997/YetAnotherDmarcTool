from fastapi import Request, status
from fastapi.responses import JSONResponse

from app.config import settings


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
