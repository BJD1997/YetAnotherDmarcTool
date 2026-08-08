import time
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

from app.config import settings

_DISCOVERY_CACHE: dict[str, tuple[float, dict]] = {}
_DISCOVERY_TTL_SECONDS = 3600
_JWKS_CLIENT_CACHE: dict[str, PyJWKClient] = {}

AUTHORIZE_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/organizations/oauth2/v2.0/token"


class TokenValidationError(Exception):
    pass


async def _get_discovery_document(tenant_id: str) -> dict:
    cached = _DISCOVERY_CACHE.get(tenant_id)
    now = time.monotonic()
    if cached and now - cached[0] < _DISCOVERY_TTL_SECONDS:
        return cached[1]

    url = f"https://login.microsoftonline.com/{tenant_id}/v2.0/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        doc = resp.json()

    _DISCOVERY_CACHE[tenant_id] = (now, doc)
    return doc


def build_authorization_url(*, state: str, code_challenge: str, redirect_uri: str) -> str:
    # Multi-tenant entry point ("organizations", not a specific tenant) —
    # we don't know which org a first-time visitor belongs to until the ID
    # token comes back with its `tid` claim.
    params = {
        "client_id": settings.entra_sso_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": "openid profile email",
        "state": state,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_tokens(*, code: str, code_verifier: str, redirect_uri: str) -> dict:
    data = {
        "client_id": settings.entra_sso_client_id,
        "client_secret": settings.entra_sso_client_secret,
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(TOKEN_URL, data=data)
        resp.raise_for_status()
        return resp.json()


async def validate_id_token(id_token: str) -> dict:
    """Validates signature, issuer, audience and expiry; returns the decoded
    claims on success.

    The `tid` claim is read from the token BEFORE signature verification
    (it has to be — it's what tells us which tenant's keys/issuer to check
    against), but that alone proves nothing: we then fetch that specific
    tenant's own discovery document to get its `jwks_uri` and `issuer`, and
    require the signature to verify against that tenant's real public keys
    AND the `iss` claim to exactly equal that tenant's real issuer URL. A
    forged token merely claiming a `tid` it doesn't belong to would fail
    signature verification, since only Microsoft holds that tenant's
    signing key — this is what "cross-checking iss embeds tid" means.
    """
    try:
        unverified = jwt.decode(id_token, options={"verify_signature": False})
    except jwt.PyJWTError as exc:
        raise TokenValidationError(f"malformed id_token: {exc}") from exc

    tenant_id = unverified.get("tid")
    if not tenant_id:
        raise TokenValidationError("id_token missing tid claim")

    discovery = await _get_discovery_document(tenant_id)
    jwks_uri = discovery["jwks_uri"]
    expected_issuer = discovery["issuer"]

    jwks_client = _JWKS_CLIENT_CACHE.get(jwks_uri)
    if jwks_client is None:
        jwks_client = PyJWKClient(jwks_uri)
        _JWKS_CLIENT_CACHE[jwks_uri] = jwks_client

    try:
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.entra_sso_client_id,
            issuer=expected_issuer,
        )
    except jwt.PyJWTError as exc:
        raise TokenValidationError(str(exc)) from exc

    if claims.get("tid") != tenant_id:
        # Can't actually happen given `iss` was just verified to encode this
        # same tenant_id, but keep the invariant explicit rather than assumed.
        raise TokenValidationError("tid mismatch after validation")

    return claims
