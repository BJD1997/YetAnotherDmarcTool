"""Settings → Sign-in: an organization's direct sign-in link, and its own
OpenID Connect provider, which org admins set up themselves. New settings
are only saved after a test sign-in with them passed. Microsoft and Google
are set by the platform admin (platform_admin.py)."""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.rls import set_org_context
from app.db.session import get_db
from app.middleware.tenant_context import get_current_user, require_org_admin
from app.models.organization import Organization
from app.models.user import User
from app.repositories.domains import verified_domain_names
from app.repositories.organizations import get_organization
from app.routers.auth_providers import checked_claims, email_on_domains, redirect_to_provider
from app.services.auth import oauth_cookies, oidc, oidc_test
from app.services.auth.oidc_test import PendingOidc
from app.services.auth.session_manager import cookie_kwargs

router = APIRouter(tags=["sign-in-settings"])


class OidcSettingsRequest(BaseModel):
    issuer: str = Field(max_length=2048)
    client_id: str = Field(min_length=1, max_length=255)
    # Left out: keep the stored secret (same issuer and client only).
    client_secret: str | None = Field(default=None, min_length=1, max_length=1024)


class OidcSaveRequest(OidcSettingsRequest):
    ticket: str = Field(max_length=4096)


def sign_in_out(org: Organization) -> dict:
    return {
        "provider": org.sign_in_provider,
        "slug": org.slug,
        "login_link": f"{settings.public_base_url}/login/{org.slug}",
        "redirect_uri": settings.oidc_redirect_uri,
        "google_workspace_domain": org.google_workspace_domain,
        "oidc": (
            {"issuer": org.oidc_issuer, "client_id": org.oidc_client_id, "secret_set": bool(org.oidc_client_secret)}
            if org.oidc_issuer
            else None
        ),
    }


def _provider(pending: PendingOidc) -> oidc.Provider:
    return oidc.Provider(pending.issuer, pending.client_id, pending.client_secret, settings.oidc_allow_private_issuers)


def _pending(org: Organization, body: OidcSettingsRequest) -> PendingOidc:
    try:
        issuer = oidc.normalize_issuer(body.issuer, allow_private=settings.oidc_allow_private_issuers)
    except oidc.OidcError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    client_id = body.client_id.strip()
    secret = body.client_secret
    if secret is None:
        # Only for the same provider and app: otherwise a changed issuer
        # could be sent the old secret.
        if org.oidc_issuer == issuer and org.oidc_client_id == client_id and org.oidc_client_secret:
            secret = org.oidc_client_secret
        else:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "enter the client secret")
    return PendingOidc(org_id=str(org.id), issuer=issuer, client_id=client_id, client_secret=secret)


def _refuse_other_provider(org: Organization) -> None:
    if org.sign_in_provider in ("microsoft", "google"):
        name = "Microsoft" if org.sign_in_provider == "microsoft" else "Google Workspace"
        raise HTTPException(status.HTTP_409_CONFLICT, f"your organization signs in with {name}; ask the platform admin to change it")


@router.get("/organizations/current/sign-in")
async def get_sign_in(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)) -> dict:
    return sign_in_out(await get_organization(db, user.organization_id))


@router.post("/organizations/current/sign-in/oidc/test")
async def start_oidc_test(
    body: OidcSettingsRequest, response: Response, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)
) -> dict:
    org = await get_organization(db, user.organization_id)
    _refuse_other_provider(org)
    pending = _pending(org, body)
    try:
        await oidc.discover(_provider(pending))
    except oidc.OidcError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Couldn't use this provider: {exc}") from exc
    response.set_cookie(
        oidc_test.COOKIE, oidc_test.pending_cookie(pending), **{**cookie_kwargs(), "max_age": oidc_test.TTL_SECONDS}
    )
    return {"start_url": "/api/auth/oidc/start-test"}


@router.put("/organizations/current/sign-in/oidc")
async def save_oidc(body: OidcSaveRequest, db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)) -> dict:
    org = await get_organization(db, user.organization_id)
    _refuse_other_provider(org)
    pending = _pending(org, body)
    if not oidc_test.ticket_matches(body.ticket, pending):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "run Test sign-in first: settings are only saved after a successful test")
    org.oidc_issuer, org.oidc_client_id, org.oidc_client_secret = pending.issuer, pending.client_id, pending.client_secret
    await db.commit()
    return sign_in_out(org)


@router.delete("/organizations/current/sign-in/oidc")
async def remove_oidc(db: AsyncSession = Depends(get_db), user: User = Depends(require_org_admin)) -> dict:
    org = await get_organization(db, user.organization_id)
    org.oidc_issuer = org.oidc_client_id = org.oidc_client_secret = None
    await db.commit()
    return sign_in_out(org)


@router.get("/auth/oidc/start-test")
async def oidc_start_test(request: Request) -> Response:
    pending = oidc_test.read_pending(request.cookies.get(oidc_test.COOKIE))
    if pending is None:
        return _report_to_settings({"ok": False, "error": "The test took too long. Run it again from Settings."})
    try:
        return await redirect_to_provider(
            _provider(pending), redirect_uri=settings.oidc_redirect_uri, login_hint=None, org_id=pending.org_id, mode="test"
        )
    except oidc.OidcError as exc:
        return _report_to_settings({"ok": False, "error": f"Couldn't use this provider: {exc}"})


def _report_to_settings(result: dict) -> HTMLResponse:
    """A small page in the test popup that hands the result to the Settings
    window (only on this server's own origin) and closes itself."""
    payload = json.dumps({"type": "yadt-oidc-test", **result})
    origin = json.dumps(settings.public_base_url)
    html = (
        "<!doctype html><meta charset=utf-8><title>Test sign-in</title>"
        f"<script>window.opener&&window.opener.postMessage({payload},{origin});window.close()</script>"
        "<p>You can close this window.</p>"
    )
    response = HTMLResponse(html)
    for name in (*oauth_cookies.NAMES, oidc_test.COOKIE):
        response.delete_cookie(name, path="/")
    return response


async def finish_oidc_test(request: Request, db: AsyncSession, cookies: oauth_cookies.OAuthCookies) -> HTMLResponse:
    """The end of a test sign-in: everything a real sign-in checks, except
    starting a session."""
    pending = oidc_test.read_pending(request.cookies.get(oidc_test.COOKIE))
    if pending is None or pending.org_id != cookies.org_id:
        return _report_to_settings({"ok": False, "error": "The test took too long or was started elsewhere. Run it again."})
    try:
        claims = await checked_claims(_provider(pending), request, cookies, settings.oidc_redirect_uri)
    except (oidc.OidcError, KeyError) as exc:
        return _report_to_settings({"ok": False, "error": f"The provider's answer didn't check out: {exc}"})
    email = str(claims.get("email") or "")
    if not email or claims.get("email_verified") is False:
        return _report_to_settings({"ok": False, "error": "The provider didn't send a verified email address."})
    org_id = uuid.UUID(pending.org_id)
    await set_org_context(db, org_id)
    if not email_on_domains(email, await verified_domain_names(db, org_id)):
        return _report_to_settings({"ok": False, "error": f"{email} isn't on one of your verified domains."})
    return _report_to_settings({"ok": True, "ticket": oidc_test.ticket_for(pending), "email": email})
