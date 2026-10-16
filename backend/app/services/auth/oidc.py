"""A generic OpenID Connect client: Google sign-in and organizations' own
providers (Authentik, Keycloak, ...). Microsoft keeps entra_oidc.py, which
handles its multi-tenant issuers.

Every request to an issuer is checked against private addresses first
(unless Provider.allow_private), never follows redirects and reads at most
MAX_RESPONSE_BYTES: the issuer URL can come from an org admin."""

import dataclasses
import secrets
import time
from collections.abc import Callable
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

from app.services.dns_checks.ssrf_guard import BlockedAddressError, assert_public_host

CACHE_SECONDS = 3600
MAX_RESPONSE_BYTES = 1_000_000
LEEWAY_SECONDS = 60
_ALLOWED_ALGORITHMS = {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"}

client_factory: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(timeout=10, follow_redirects=False)

_discovery_cache: dict[str, tuple[float, dict]] = {}
_jwks_cache: dict[str, tuple[float, jwt.PyJWKSet]] = {}


class OidcError(Exception):
    """Anything that makes a sign-in through this provider impossible; the
    message is safe to show to the admin setting it up."""


@dataclasses.dataclass(frozen=True)
class Provider:
    issuer: str
    client_id: str
    client_secret: str
    allow_private: bool = False


def clear_caches() -> None:
    _discovery_cache.clear()
    _jwks_cache.clear()


def normalize_issuer(value: str, *, allow_private: bool = False) -> str:
    issuer = value.strip()
    if issuer.endswith("/"):
        issuer = issuer[:-1]
    parts = urlsplit(issuer)
    allowed_schemes = {"https", "http"} if allow_private else {"https"}
    if parts.scheme not in allowed_schemes or not parts.hostname or parts.query or parts.fragment:
        raise OidcError("the issuer must be an https:// address, for example https://auth.example.com/application/o/yadt")
    return issuer


async def _checked_url(url: str, provider: Provider) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" and not (provider.allow_private and parts.scheme == "http"):
        raise OidcError(f"{url} isn't an https:// address")
    if not provider.allow_private:
        try:
            await assert_public_host(parts.hostname or "")
        except BlockedAddressError as exc:
            raise OidcError(str(exc)) from exc
    return url


def _json(response: httpx.Response, what: str) -> dict:
    if response.status_code != 200:
        raise OidcError(f"{what} answered HTTP {response.status_code}")
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise OidcError(f"{what} is too large")
    try:
        data = response.json()
    except ValueError as exc:
        raise OidcError(f"{what} isn't JSON") from exc
    if not isinstance(data, dict):
        raise OidcError(f"{what} isn't a JSON object")
    return data


async def _get(url: str, provider: Provider, what: str) -> dict:
    await _checked_url(url, provider)
    try:
        async with client_factory() as client:
            return _json(await client.get(url), what)
    except httpx.HTTPError as exc:
        raise OidcError(f"couldn't reach {what}: {exc.__class__.__name__}") from exc


async def discover(provider: Provider) -> dict:
    cached = _discovery_cache.get(provider.issuer)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    doc = await _get(f"{provider.issuer}/.well-known/openid-configuration", provider, "the discovery document")
    if doc.get("issuer") not in (provider.issuer, provider.issuer + "/"):
        raise OidcError(f"the discovery document is for {doc.get('issuer')!r}, not {provider.issuer!r}")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        if not isinstance(doc.get(key), str):
            raise OidcError(f"the discovery document has no {key}")
    _discovery_cache[provider.issuer] = (time.monotonic(), doc)
    return doc


def authorization_url(
    discovery: dict,
    provider: Provider,
    *,
    redirect_uri: str,
    state: str,
    nonce: str,
    code_challenge: str,
    login_hint: str | None = None,
    extra: dict[str, str] | None = None,
) -> str:
    params = {
        "client_id": provider.client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "scope": "openid email profile",
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        **({"login_hint": login_hint} if login_hint else {}),
        **(extra or {}),
    }
    return f"{discovery['authorization_endpoint']}?{urlencode(params)}"


async def exchange_code(discovery: dict, provider: Provider, *, code: str, code_verifier: str, redirect_uri: str) -> dict:
    url = await _checked_url(discovery["token_endpoint"], provider)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
        "client_id": provider.client_id,
        "client_secret": provider.client_secret,
    }
    try:
        async with client_factory() as client:
            tokens = _json(await client.post(url, data=data), "the token endpoint")
    except httpx.HTTPError as exc:
        raise OidcError(f"couldn't reach the token endpoint: {exc.__class__.__name__}") from exc
    if not isinstance(tokens.get("id_token"), str):
        raise OidcError("the token endpoint returned no id_token")
    return tokens


async def _jwks(discovery: dict, provider: Provider, *, refresh: bool = False) -> jwt.PyJWKSet:
    uri = discovery["jwks_uri"]
    cached = _jwks_cache.get(uri)
    if cached and not refresh and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    try:
        keys = jwt.PyJWKSet.from_dict(await _get(uri, provider, "the signing keys"))
    except jwt.PyJWTError as exc:
        raise OidcError(f"the signing keys can't be read: {exc}") from exc
    _jwks_cache[uri] = (time.monotonic(), keys)
    return keys


async def _signing_key(discovery: dict, provider: Provider, id_token: str) -> jwt.PyJWK:
    try:
        kid = jwt.get_unverified_header(id_token).get("kid")
    except jwt.PyJWTError as exc:
        raise OidcError(f"malformed id_token: {exc}") from exc
    for refresh in (False, True):  # a new key: fetch the set once more
        keys = await _jwks(discovery, provider, refresh=refresh)
        matches = [k for k in keys.keys if kid is None or k.key_id == kid]
        if matches:
            return matches[0]
    raise OidcError("the id_token is signed with an unknown key")


async def validate_id_token(discovery: dict, provider: Provider, id_token: str, *, nonce: str) -> dict:
    key = await _signing_key(discovery, provider, id_token)
    supported = set(discovery.get("id_token_signing_alg_values_supported") or ["RS256"])
    algorithms = sorted(supported & _ALLOWED_ALGORITHMS) or ["RS256"]
    try:
        claims = jwt.decode(
            id_token,
            key.key,
            algorithms=algorithms,
            audience=provider.client_id,
            issuer=discovery["issuer"],
            leeway=LEEWAY_SECONDS,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise OidcError(f"the id_token isn't valid: {exc}") from exc
    audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
    if len(audiences) > 1 and claims.get("azp") != provider.client_id:
        raise OidcError("the id_token is meant for more than this app")
    if not isinstance(claims.get("nonce"), str) or not secrets.compare_digest(claims["nonce"].encode(), nonce.encode()):
        raise OidcError("the id_token doesn't belong to this sign-in")
    return claims
