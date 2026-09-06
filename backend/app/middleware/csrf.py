from fastapi import Request
from fastapi.responses import JSONResponse

# CSRF defense: cookies are SameSite=Lax (blocks cross-site *form* submits,
# but not e.g. a cross-site <script> doing a same-site-adjacent GET-triggered
# nav). State-changing requests additionally require this custom header,
# which only same-origin `fetch()`/XHR can set — a bare cross-site form POST
# cannot. The OAuth callback is a real cross-site GET navigation from
# Microsoft and is exempted (GETs are excluded below anyway; it does nothing
# state-changing on its own request line besides setting the session cookie
# it just issued, which is the intended, unauthenticated-by-design step of
# the login flow itself).
CSRF_HEADER = "X-Requested-With"
CSRF_HEADER_VALUE = "yetanotherdmarctool"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


async def enforce_csrf_header(request: Request, call_next):
    if (
        request.method in UNSAFE_METHODS
        and request.url.path.startswith("/api/")
        and request.headers.get(CSRF_HEADER) != CSRF_HEADER_VALUE
    ):
        return JSONResponse({"detail": "missing or invalid X-Requested-With header"}, status_code=403)
    return await call_next(request)
