from fastapi import Request

from app.config import settings

# Response hardening headers applied to every response. Only the CSP
# directives that can't affect script/style/img loading are set here, so this
# can't break the SPA: frame-ancestors (clickjacking), base-uri (<base>
# injection), object-src (plugin embedding), form-action (form hijacking). A
# full script-src/style-src CSP is a deliberate follow-up — index.html has an
# inline theme-bootstrap <script> that would need its sha256 hash allow-listed
# first, and there's no way to verify a strict CSP doesn't break rendering
# without a browser in the loop.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "frame-ancestors 'none'; base-uri 'self'; object-src 'none'; form-action 'self'",
}


async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    # HSTS only when this instance is actually served over HTTPS (every real
    # deployment, behind NPM) — never on plain-http localhost smoke testing.
    if settings.public_base_url.startswith("https://"):
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response
