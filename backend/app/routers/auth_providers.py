"""Sign-in with Google Workspace and with an organization's own OpenID
Connect provider. Microsoft stays in auth.py. Both end in
sign_in_flow.complete_sign_in."""

import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.rls import set_org_context
from app.db.session import get_db
from app.models.enums import AuthMethod, OrganizationStatus, SignInResult
from app.models.organization import Organization
from app.repositories.auth import get_organization_by_google_domain, get_organization_by_slug
from app.repositories.domains import verified_domain_names
from app.repositories.organizations import get_organization
from app.services.auth import oauth_cookies, oidc, pkce
from app.services.auth.sign_in_flow import complete_sign_in, session_redirect
from app.services.auth.sign_in_log import client_network_info, record_sign_in_event

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_ISSUER = "https://accounts.google.com"


def google_provider() -> oidc.Provider:
    return oidc.Provider(
        issuer=GOOGLE_ISSUER,
        client_id=settings.google_sso_client_id or "",
        client_secret=settings.google_sso_client_secret or "",
    )


def org_provider(org: Organization) -> oidc.Provider:
    return oidc.Provider(
        issuer=org.oidc_issuer or "",
        client_id=org.oidc_client_id or "",
        client_secret=org.oidc_client_secret or "",
        allow_private=settings.oidc_allow_private_issuers,
    )


def email_on_domains(email: str, domains: list[str]) -> bool:
    domain = email.rpartition("@")[2].strip().lower().rstrip(".")
    return bool(domain) and any(domain == d or domain.endswith("." + d) for d in domains)


def _back_to_login(error: str) -> RedirectResponse:
    response = RedirectResponse(f"/login?error={error}", status_code=302)
    for name in oauth_cookies.NAMES:
        response.delete_cookie(name, path="/")
    return response


async def _failure(
    db: AsyncSession, request: Request, method: AuthMethod, reason: str, *, org_id=None, email: str | None = None
) -> RedirectResponse:
    ip_address, user_agent = client_network_info(request)
    await record_sign_in_event(
        db, result=SignInResult.failure, auth_method=method, organization_id=org_id, attempted_email=email,
        failure_reason=reason, ip_address=ip_address, user_agent=user_agent,
    )
    await db.commit()
    return _back_to_login(reason)


async def redirect_to_provider(
    provider: oidc.Provider,
    *,
    redirect_uri: str,
    login_hint: str | None,
    org_id: str | None,
    mode: str = "sign_in",
    extra: dict[str, str] | None = None,
) -> RedirectResponse:
    doc = await oidc.discover(provider)
    state, nonce, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(24), pkce.generate_verifier()
    url = oidc.authorization_url(
        doc, provider, redirect_uri=redirect_uri, state=state, nonce=nonce,
        code_challenge=pkce.derive_challenge(verifier), login_hint=login_hint, extra=extra,
    )
    response = RedirectResponse(url, status_code=302)
    oauth_cookies.start(response, state=state, verifier=verifier, nonce=nonce, org_id=org_id, mode=mode)
    return response


async def checked_claims(provider: oidc.Provider, request: Request, cookies: oauth_cookies.OAuthCookies, redirect_uri: str) -> dict:
    """The provider's ID token claims; raises oidc.OidcError when its answer
    doesn't check out."""
    if request.query_params.get("error") or not request.query_params.get("code"):
        raise oidc.OidcError(request.query_params.get("error") or "no code")
    doc = await oidc.discover(provider)
    tokens = await oidc.exchange_code(
        doc, provider, code=request.query_params["code"], code_verifier=cookies.verifier, redirect_uri=redirect_uri
    )
    return await oidc.validate_id_token(doc, provider, tokens["id_token"], nonce=cookies.nonce)


# --- Google ------------------------------------------------------------------

@router.get("/google/login")
async def google_login(login_hint: str | None = Query(default=None, max_length=320)) -> RedirectResponse:
    if not settings.google_sso_client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    try:
        # hd=*: only Google Workspace accounts, not personal Gmail.
        return await redirect_to_provider(
            google_provider(), redirect_uri=settings.google_sso_redirect_uri, login_hint=login_hint, org_id=None,
            extra={"hd": "*"},
        )
    except oidc.OidcError:
        return _back_to_login("provider_unavailable")


@router.get("/google/callback")
async def google_callback(request: Request, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    method = AuthMethod.google
    cookies = oauth_cookies.read(request)
    if cookies is None or not settings.google_sso_client_id:
        return await _failure(db, request, method, "invalid_state")
    try:
        claims = await checked_claims(google_provider(), request, cookies, settings.google_sso_redirect_uri)
    except (oidc.OidcError, KeyError):
        return await _failure(db, request, method, "token_invalid")

    email = str(claims.get("email") or "")
    if claims.get("email_verified") is not True:
        return await _failure(db, request, method, "email_not_verified", email=email)
    # Google sets hd only for Workspace accounts: personal Gmail has none.
    workspace = str(claims.get("hd") or "").lower()
    if not workspace:
        return await _failure(db, request, method, "hd_mismatch", email=email)
    org = await get_organization_by_google_domain(db, workspace)
    if org is None:
        return await _failure(db, request, method, "organization_not_provisioned", email=email)

    ip_address, user_agent = client_network_info(request)
    outcome = await complete_sign_in(
        db, org=org, method=method, subject=str(claims["sub"]), email=email, display_name=claims.get("name"),
        ip_address=ip_address, user_agent=user_agent,
    )
    return session_redirect(outcome, clear_cookies=oauth_cookies.NAMES)


# --- an organization's own provider ------------------------------------------

@router.get("/oidc/start")
async def oidc_start(
    org: str = Query(max_length=63),
    login_hint: str | None = Query(default=None, max_length=320),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    organization = await get_organization_by_slug(db, org)
    if organization is None or organization.sign_in_provider != "oidc" or organization.status != OrganizationStatus.active:
        return _back_to_login("organization_not_provisioned")
    try:
        return await redirect_to_provider(
            org_provider(organization), redirect_uri=settings.oidc_redirect_uri, login_hint=login_hint,
            org_id=str(organization.id),
        )
    except oidc.OidcError:
        return _back_to_login("provider_unavailable")


@router.get("/oidc/callback")
async def oidc_callback(request: Request, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    method = AuthMethod.oidc
    cookies = oauth_cookies.read(request)
    if cookies is None or not cookies.org_id:
        return await _failure(db, request, method, "invalid_state")
    if cookies.mode == "test":
        from app.routers.organizations import finish_oidc_test

        return await finish_oidc_test(request, db, cookies)

    try:
        org = await get_organization(db, uuid.UUID(cookies.org_id))
    except ValueError:
        org = None
    if org is None or org.sign_in_provider != "oidc":
        return await _failure(db, request, method, "organization_not_provisioned")
    # Failures below are logged against this organization.
    await set_org_context(db, org.id)
    try:
        claims = await checked_claims(org_provider(org), request, cookies, settings.oidc_redirect_uri)
    except (oidc.OidcError, KeyError):
        return await _failure(db, request, method, "token_invalid", org_id=org.id)

    email = str(claims.get("email") or "")
    if not email or claims.get("email_verified") is False:
        return await _failure(db, request, method, "email_not_verified", org_id=org.id, email=email)
    if not email_on_domains(email, await verified_domain_names(db, org.id)):
        return await _failure(db, request, method, "email_domain_not_verified", org_id=org.id, email=email)

    ip_address, user_agent = client_network_info(request)
    outcome = await complete_sign_in(
        db, org=org, method=method, subject=str(claims["sub"]), email=email,
        display_name=claims.get("name") or claims.get("preferred_username"), ip_address=ip_address, user_agent=user_agent,
    )
    return session_redirect(outcome, clear_cookies=oauth_cookies.NAMES)
