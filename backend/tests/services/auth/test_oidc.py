"""The generic OIDC client against a fake provider: locally generated keys,
discovery and JWKS served by an httpx MockTransport."""

import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.services.auth import oidc

ISSUER = "https://idp.example.com/realms/acme"
CLIENT = "yadt"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk() -> dict:
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(KEY.public_key(), as_dict=True)
    return {**jwk, "kid": "k1", "use": "sig", "alg": "RS256"}


DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/auth",
    "token_endpoint": f"{ISSUER}/token",
    "jwks_uri": f"{ISSUER}/certs",
    "id_token_signing_alg_values_supported": ["RS256"],
}


def _token(key=KEY, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": CLIENT, "sub": "user-1", "iat": now, "exp": now + 300, "nonce": "n1",
        "email": "jan@acme.example", "email_verified": True, **overrides,
    }
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


@pytest.fixture(autouse=True)
def fake_idp(monkeypatch):
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=DISCOVERY)
        if request.url.path.endswith("/certs"):
            return httpx.Response(200, json={"keys": [_jwk()]})
        if request.url.path.endswith("/token"):
            return httpx.Response(200, json={"id_token": _token(), "access_token": "a"})
        return httpx.Response(404)

    async def _public(host):
        return None

    oidc.clear_caches()
    monkeypatch.setattr(oidc, "client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(oidc, "assert_public_host", _public)
    return requests


PROVIDER = oidc.Provider(issuer=ISSUER, client_id=CLIENT, client_secret="s3cret")


async def test_a_valid_id_token_is_accepted():
    doc = await oidc.discover(PROVIDER)
    claims = await oidc.validate_id_token(doc, PROVIDER, _token(), nonce="n1")
    assert claims["sub"] == "user-1"


@pytest.mark.parametrize(
    "overrides,nonce",
    [
        ({"iss": "https://evil.example"}, "n1"),
        ({"aud": "someone-else"}, "n1"),
        ({"exp": int(time.time()) - 3600}, "n1"),
        ({}, "other-nonce"),
        ({"sub": None}, "n1"),
    ],
)
async def test_a_wrong_token_is_refused(overrides, nonce):
    doc = await oidc.discover(PROVIDER)
    with pytest.raises(oidc.OidcError):
        await oidc.validate_id_token(doc, PROVIDER, _token(**overrides), nonce=nonce)


async def test_a_token_signed_with_another_key_is_refused():
    doc = await oidc.discover(PROVIDER)
    with pytest.raises(oidc.OidcError):
        await oidc.validate_id_token(doc, PROVIDER, _token(key=OTHER_KEY), nonce="n1")


async def test_several_audiences_need_azp_to_be_us():
    doc = await oidc.discover(PROVIDER)
    with pytest.raises(oidc.OidcError):
        await oidc.validate_id_token(doc, PROVIDER, _token(aud=[CLIENT, "other"]), nonce="n1")
    claims = await oidc.validate_id_token(doc, PROVIDER, _token(aud=[CLIENT, "other"], azp=CLIENT), nonce="n1")
    assert claims["sub"] == "user-1"


async def test_discovery_must_name_the_same_issuer():
    with pytest.raises(oidc.OidcError):
        await oidc.discover(oidc.Provider(issuer="https://idp.example.com/realms/other", client_id=CLIENT, client_secret="s"))


async def test_a_private_issuer_is_refused_unless_allowed(monkeypatch):
    from app.services.dns_checks.ssrf_guard import BlockedAddressError

    async def _private(host):
        raise BlockedAddressError(f"{host} resolves to a non-public address")

    monkeypatch.setattr(oidc, "assert_public_host", _private)
    with pytest.raises(oidc.OidcError, match="non-public"):
        await oidc.discover(PROVIDER)
    allowed = oidc.Provider(issuer=ISSUER, client_id=CLIENT, client_secret="s", allow_private=True)
    assert (await oidc.discover(allowed))["issuer"] == ISSUER


def test_issuers_must_be_https():
    assert oidc.normalize_issuer(" https://idp.example.com/realms/acme/ ") == ISSUER
    with pytest.raises(oidc.OidcError):
        oidc.normalize_issuer("http://idp.example.com")
    assert (
        oidc.normalize_issuer("http://10.0.0.5:9000/application/o/yadt/", allow_private=True)
        == "http://10.0.0.5:9000/application/o/yadt"
    )


async def test_the_code_exchange_sends_the_secret_and_verifier(fake_idp):
    doc = await oidc.discover(PROVIDER)
    tokens = await oidc.exchange_code(doc, PROVIDER, code="c", code_verifier="v", redirect_uri="https://yadt.example/cb")
    assert "id_token" in tokens
    body = fake_idp[-1].content.decode()
    assert "client_secret=s3cret" in body and "code_verifier=v" in body


async def test_the_authorization_url_carries_pkce_nonce_and_hint():
    doc = await oidc.discover(PROVIDER)
    url = oidc.authorization_url(
        doc, PROVIDER, redirect_uri="https://yadt.example/cb", state="s", nonce="n", code_challenge="c",
        login_hint="jan@acme.example",
    )
    assert url.startswith(f"{ISSUER}/auth?")
    for part in ("code_challenge_method=S256", "nonce=n", "login_hint=jan%40acme.example", "scope=openid+email+profile"):
        assert part in url


async def test_the_providers_own_error_is_passed_on(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(200, json=DISCOVERY)
        return httpx.Response(400, json={"error": "invalid_client", "error_description": "Client authentication failed"})

    monkeypatch.setattr(oidc, "client_factory", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    doc = await oidc.discover(PROVIDER)
    with pytest.raises(oidc.OidcError, match=r"HTTP 400 \(invalid_client: Client authentication failed\)"):
        await oidc.exchange_code(doc, PROVIDER, code="c", code_verifier="v", redirect_uri="https://yadt.example/cb")
