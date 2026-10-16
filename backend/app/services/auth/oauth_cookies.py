"""The short-lived cookies that tie a provider's answer to the browser that
started the sign-in: state (CSRF), PKCE verifier, nonce, and for an
organization's own provider which organization it was started for. Used by
Google and OIDC sign-in; Microsoft sets the same first three in auth.py."""

import dataclasses
import secrets

from fastapi import Request, Response

from app.services.auth.session_manager import cookie_kwargs

STATE, VERIFIER, NONCE, ORG, MODE = "oauth_state", "oauth_verifier", "oauth_nonce", "oauth_org", "oauth_mode"
NAMES = (STATE, VERIFIER, NONCE, ORG, MODE)
_MAX_AGE = 600


@dataclasses.dataclass(frozen=True)
class OAuthCookies:
    verifier: str
    nonce: str
    org_id: str | None
    mode: str


def start(response: Response, *, state: str, verifier: str, nonce: str, org_id: str | None = None, mode: str = "sign_in") -> None:
    short_lived = {**cookie_kwargs(), "max_age": _MAX_AGE}
    response.set_cookie(STATE, state, **short_lived)
    response.set_cookie(VERIFIER, verifier, **short_lived)
    response.set_cookie(NONCE, nonce, **short_lived)
    response.set_cookie(MODE, mode, **short_lived)
    if org_id:
        response.set_cookie(ORG, org_id, **short_lived)
    else:
        response.delete_cookie(ORG, path="/")


def read(request: Request) -> OAuthCookies | None:
    """The cookies, or None when the state doesn't match the provider's
    answer (a forged or replayed callback)."""
    state = request.query_params.get("state")
    cookie_state = request.cookies.get(STATE)
    verifier = request.cookies.get(VERIFIER)
    nonce = request.cookies.get(NONCE)
    if not state or not cookie_state or not verifier or not nonce:
        return None
    if not secrets.compare_digest(state.encode(), cookie_state.encode()):
        return None
    return OAuthCookies(verifier, nonce, request.cookies.get(ORG), request.cookies.get(MODE) or "sign_in")
