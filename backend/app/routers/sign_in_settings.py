"""Settings → Sign-in: an organization's direct sign-in link and its sign-in
provider. Only the organization's break-glass account can choose, switch
or remove the provider (Microsoft, Google Workspace or its own OpenID
Connect provider), and only after a test sign-in with it passed: the test
account's email must be on one of the organization's verified domains, and
for Microsoft and Google the tenant or Workspace is taken from the
provider's signed token, never typed in. The platform admin can also set
a provider directly (platform_admin.py)."""

import json
import secrets
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.rls import set_org_context
from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_break_glass
from app.models.enums import AuthMethod, UserStatus
from app.models.organization import Organization
from app.models.user import User
from app.repositories.auth import get_organization_by_entra_tenant_id, get_organization_by_google_domain
from app.repositories.domains import verified_domain_names
from app.repositories.organizations import get_organization
from app.repositories.users import list_users_for_org
from app.routers.auth_providers import checked_claims, email_on_domains, google_provider, redirect_to_provider
from app.services.auth import entra_oidc, oauth_cookies, oidc, pkce, session_manager, sign_in_test
from app.services.auth.session_manager import cookie_kwargs
from app.services.auth.sign_in_test import Passed, PendingSignIn

router = APIRouter(tags=["sign-in-settings"])

PROVIDER_NAMES = {"microsoft": "Microsoft", "google": "Google Workspace", "oidc": "your OpenID Connect provider"}


class ProviderRequest(BaseModel):
    provider: str = Field(pattern="^(microsoft|google|oidc)$")
    # OIDC only:
    issuer: str | None = Field(default=None, max_length=2048)
    client_id: str | None = Field(default=None, max_length=255)
    # Left out: keep the stored secret (same issuer and client only).
    client_secret: str | None = Field(default=None, min_length=1, max_length=1024)


class ProviderSaveRequest(ProviderRequest):
    ticket: str = Field(max_length=4096)


def sign_in_out(org: Organization, user: User | None = None) -> dict:
    return {
        "provider": org.sign_in_provider,
        "slug": org.slug,
        "login_link": f"{settings.public_base_url}/login/{org.slug}",
        "redirect_uri": settings.oidc_redirect_uri,
        "entra_tenant_id": str(org.entra_tenant_id) if org.entra_tenant_id else None,
        "google_workspace_domain": org.google_workspace_domain,
        "oidc": (
            {"issuer": org.oidc_issuer, "client_id": org.oidc_client_id, "secret_set": bool(org.oidc_client_secret)}
            if org.oidc_issuer
            else None
        ),
        # Which providers this server offers, and whether this session may change it.
        "available": {
            "microsoft": bool(settings.entra_sso_client_id),
            "google": bool(settings.google_sso_client_id),
            "oidc": True,
        },
        "can_change": bool(user and user.is_break_glass and user.auth_method == AuthMethod.local),
    }


def _oidc_provider(pending: PendingSignIn) -> oidc.Provider:
    return oidc.Provider(pending.issuer, pending.client_id, pending.client_secret, settings.oidc_allow_private_issuers)


def _pending(org: Organization, body: ProviderRequest) -> PendingSignIn:
    if body.provider == "microsoft" and not settings.entra_sso_client_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "this server has no Microsoft sign-in app; ask the platform admin")
    if body.provider == "google" and not settings.google_sso_client_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "this server has no Google sign-in app; ask the platform admin")
    if body.provider != "oidc":
        return PendingSignIn(org_id=str(org.id), provider=body.provider)
    try:
        issuer = oidc.normalize_issuer(body.issuer or "", allow_private=settings.oidc_allow_private_issuers)
    except oidc.OidcError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    client_id = (body.client_id or "").strip()
    if not client_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "enter the client ID")
    secret = body.client_secret
    if secret is None:
        # Only for the same provider and app: otherwise a changed issuer
        # could be sent the old secret.
        if org.oidc_issuer == issuer and org.oidc_client_id == client_id and org.oidc_client_secret:
            secret = org.oidc_client_secret
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "enter the client secret")
    return PendingSignIn(org_id=str(org.id), provider="oidc", issuer=issuer, client_id=client_id, client_secret=secret)


@router.get("/organizations/current/sign-in")
async def get_sign_in(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    return sign_in_out(await get_organization(db, user.organization_id), user)


@router.post("/organizations/current/sign-in/test")
async def start_test(
    body: ProviderRequest, response: Response, db: AsyncSession = Depends(get_db), user: User = Depends(require_break_glass)
) -> dict:
    org = await get_organization(db, user.organization_id)
    pending = _pending(org, body)
    if pending.provider == "oidc":
        try:
            await oidc.discover(_oidc_provider(pending))
        except oidc.OidcError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Couldn't use this provider: {exc}") from exc
    response.set_cookie(
        sign_in_test.COOKIE, sign_in_test.pending_cookie(pending), **{**cookie_kwargs(), "max_age": sign_in_test.TTL_SECONDS}
    )
    return {"start_url": "/api/auth/sign-in-test/start"}


@router.get("/auth/sign-in-test/start")
async def test_start(request: Request) -> Response:
    """Opened in the test window: off to the provider being tested."""
    pending = sign_in_test.read_pending(request.cookies.get(sign_in_test.COOKIE))
    if pending is None:
        return _report({"ok": False, "error": "The test took too long. Run it again from Settings."})
    try:
        if pending.provider == "microsoft":
            state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), pkce.generate_verifier()
            url = entra_oidc.build_authorization_url(
                state=state, nonce=nonce, code_challenge=pkce.derive_challenge(verifier),
                redirect_uri=settings.entra_sso_redirect_uri,
            )
            response = RedirectResponse(url, status_code=302)
            oauth_cookies.start(response, state=state, verifier=verifier, nonce=nonce, org_id=pending.org_id, mode="test")
            return response
        if pending.provider == "google":
            return await redirect_to_provider(
                google_provider(), redirect_uri=settings.google_sso_redirect_uri, login_hint=None,
                org_id=pending.org_id, mode="test", extra={"hd": "*"},
            )
        return await redirect_to_provider(
            _oidc_provider(pending), redirect_uri=settings.oidc_redirect_uri, login_hint=None,
            org_id=pending.org_id, mode="test",
        )
    except oidc.OidcError as exc:
        return _report({"ok": False, "error": f"Couldn't use this provider: {exc}"})


def _report(result: dict) -> HTMLResponse:
    """A small page in the test window that hands the result to the
    Settings window (only on this server's own origin) and closes itself."""
    payload = json.dumps({"type": "yadt-sign-in-test", **result})
    origin = json.dumps(settings.public_base_url)
    html = (
        "<!doctype html><meta charset=utf-8><title>Test sign-in</title>"
        f"<script>window.opener&&window.opener.postMessage({payload},{origin});window.close()</script>"
        "<p>You can close this window.</p>"
    )
    response = HTMLResponse(html)
    for name in (*oauth_cookies.NAMES, sign_in_test.COOKIE):
        response.delete_cookie(name, path="/")
    return response


async def _claims(pending: PendingSignIn, request: Request, cookies: oauth_cookies.OAuthCookies) -> dict:
    if pending.provider == "microsoft":
        if request.query_params.get("error") or not request.query_params.get("code"):
            raise oidc.OidcError(request.query_params.get("error") or "no code")
        try:
            tokens = await entra_oidc.exchange_code_for_tokens(
                code=request.query_params["code"], code_verifier=cookies.verifier, redirect_uri=settings.entra_sso_redirect_uri
            )
            claims = await entra_oidc.validate_id_token(tokens["id_token"])
        except (entra_oidc.TokenValidationError, KeyError, httpx.HTTPError) as exc:
            raise oidc.OidcError(str(exc) or exc.__class__.__name__) from exc
        if not secrets.compare_digest(str(claims.get("nonce", "")).encode(), cookies.nonce.encode()):
            raise oidc.OidcError("the token doesn't belong to this sign-in")
        return {**claims, "email": claims.get("preferred_username") or claims.get("email")}
    if pending.provider == "google":
        return await checked_claims(google_provider(), request, cookies, settings.google_sso_redirect_uri)
    return await checked_claims(_oidc_provider(pending), request, cookies, settings.oidc_redirect_uri)


async def finish_test(request: Request, db: AsyncSession, cookies: oauth_cookies.OAuthCookies) -> HTMLResponse:
    """The end of a test sign-in (any provider's callback in test mode):
    everything a real sign-in checks, except starting a session."""
    pending = sign_in_test.read_pending(request.cookies.get(sign_in_test.COOKIE))
    if pending is None or pending.org_id != cookies.org_id:
        return _report({"ok": False, "error": "The test took too long or was started elsewhere. Run it again."})
    try:
        claims = await _claims(pending, request, cookies)
    except (oidc.OidcError, KeyError) as exc:
        return _report({"ok": False, "error": f"The provider's answer didn't check out: {exc}"})

    email = str(claims.get("email") or "")
    if not email or claims.get("email_verified") is False or (pending.provider == "google" and claims.get("email_verified") is not True):
        return _report({"ok": False, "error": "The provider didn't send a verified email address."})
    org_id = uuid.UUID(pending.org_id)
    await set_org_context(db, org_id)
    if not email_on_domains(email, await verified_domain_names(db, org_id)):
        return _report({"ok": False, "error": f"{email} isn't on one of your verified domains."})

    passed = Passed()
    if pending.provider == "microsoft":
        passed = Passed(tenant_id=str(claims["tid"]))
        other = await get_organization_by_entra_tenant_id(db, passed.tenant_id)
        if other is not None and other.id != org_id:
            return _report({"ok": False, "error": "This Microsoft tenant is already used by another organization."})
    elif pending.provider == "google":
        workspace = str(claims.get("hd") or "").lower()
        if not workspace:
            return _report({"ok": False, "error": "Sign in with a Google Workspace account, not a personal Google account."})
        passed = Passed(workspace=workspace)
        other = await get_organization_by_google_domain(db, workspace)
        if other is not None and other.id != org_id:
            return _report({"ok": False, "error": "This Google Workspace is already used by another organization."})
    return _report({
        "ok": True, "ticket": sign_in_test.ticket_for(pending, passed), "email": email,
        "tenant_id": passed.tenant_id, "workspace": passed.workspace,
    })


async def _end_provider_sessions(db: AsyncSession, org: Organization) -> None:
    """After a provider change: everyone signed in through a provider signs
    in again, and other local accounts stop working (only the break-glass
    account stays local once there's a provider)."""
    for member in await list_users_for_org(db, org.id):
        if member.auth_method != AuthMethod.local:
            await session_manager.revoke_user_sessions(db, member.id)
        elif not member.is_break_glass and org.sign_in_provider != "none":
            member.status = UserStatus.disabled
            await session_manager.revoke_user_sessions(db, member.id)


@router.put("/organizations/current/sign-in")
async def save_provider(
    body: ProviderSaveRequest, db: AsyncSession = Depends(get_db), user: User = Depends(require_break_glass)
) -> dict:
    org = await get_organization(db, user.organization_id)
    pending = _pending(org, body)
    passed = sign_in_test.read_ticket(body.ticket, pending)
    if passed is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "run Test sign-in first: a provider is only saved after a successful test")

    if pending.provider == "microsoft":
        other = await get_organization_by_entra_tenant_id(db, passed.tenant_id)
        if other is not None and other.id != org.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "this Microsoft tenant is already used by another organization")
    if pending.provider == "google":
        other = await get_organization_by_google_domain(db, passed.workspace)
        if other is not None and other.id != org.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "this Google Workspace is already used by another organization")

    org.entra_tenant_id = uuid.UUID(passed.tenant_id) if pending.provider == "microsoft" else None
    org.google_workspace_domain = passed.workspace if pending.provider == "google" else None
    if pending.provider == "oidc":
        org.oidc_issuer, org.oidc_client_id, org.oidc_client_secret = pending.issuer, pending.client_id, pending.client_secret
    else:
        org.oidc_issuer = org.oidc_client_id = org.oidc_client_secret = None
    await db.flush()
    await _end_provider_sessions(db, org)
    out = sign_in_out(org, user)
    await db.commit()
    return out


@router.delete("/organizations/current/sign-in")
async def remove_provider(db: AsyncSession = Depends(get_db), user: User = Depends(require_break_glass)) -> dict:
    """Back to local accounts only."""
    org = await get_organization(db, user.organization_id)
    org.entra_tenant_id = None
    org.google_workspace_domain = None
    org.oidc_issuer = org.oidc_client_id = org.oidc_client_secret = None
    await db.flush()
    await _end_provider_sessions(db, org)
    out = sign_in_out(org, user)
    await db.commit()
    return out
