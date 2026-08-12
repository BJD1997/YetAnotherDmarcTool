import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.rls import set_org_context, set_platform_admin_context
from app.db.session import get_db
from app.middleware.tenant_context import get_current_user
from app.models.enums import AuthMethod, OrganizationStatus, SignInResult, UserRole, UserStatus
from app.models.mfa_pending_challenge import MfaPendingChallenge
from app.models.organization import Organization
from app.models.password_setup_token import PasswordSetupToken
from app.models.user import User
from app.models.user_recovery_code import UserRecoveryCode
from app.services.auth import entra_oidc, pkce, session_manager, totp
from app.services.auth.password import dummy_verify, hash_password, verify_password
from app.services.auth.rate_limit import login_limiter, otp_limiter, rate_limiter
from app.services.auth.session_manager import cookie_kwargs
from app.services.auth.sign_in_log import client_network_info, record_sign_in_event
from app.services.auth.tokens import hash_token, new_opaque_token

router = APIRouter(prefix="/auth", tags=["auth"])

_OAUTH_STATE_COOKIE = "oauth_state"
_OAUTH_VERIFIER_COOKIE = "oauth_verifier"
_MFA_PENDING_COOKIE = settings.mfa_pending_cookie_name


@router.get("/config")
async def auth_config() -> dict:
    """Unauthenticated — the login page calls this before a user exists to
    decide whether to show the "Sign in with Microsoft" option at all."""
    return {"entra_sso_enabled": bool(settings.entra_sso_client_id)}


@router.get("/login")
async def login() -> RedirectResponse:
    if not settings.entra_sso_client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    state = secrets.token_urlsafe(24)
    verifier = pkce.generate_verifier()
    challenge = pkce.derive_challenge(verifier)

    auth_url = entra_oidc.build_authorization_url(
        state=state, code_challenge=challenge, redirect_uri=settings.entra_sso_redirect_uri
    )
    response = RedirectResponse(auth_url, status_code=302)
    short_lived = {**cookie_kwargs(), "max_age": 600}
    response.set_cookie(_OAUTH_STATE_COOKIE, state, **short_lived)
    response.set_cookie(_OAUTH_VERIFIER_COOKIE, verifier, **short_lived)
    return response


@router.get("/callback")
async def callback(request: Request, db: AsyncSession = Depends(get_db)) -> RedirectResponse:
    ip_address, user_agent = client_network_info(request)

    if request.query_params.get("error"):
        await record_sign_in_event(
            db, result=SignInResult.failure, auth_method=AuthMethod.entra,
            failure_reason="entra_error", ip_address=ip_address, user_agent=user_agent,
        )
        await db.commit()
        return RedirectResponse(f"/login?error={request.query_params['error']}", status_code=302)

    code = request.query_params.get("code")
    state = request.query_params.get("state")
    cookie_state = request.cookies.get(_OAUTH_STATE_COOKIE)
    verifier = request.cookies.get(_OAUTH_VERIFIER_COOKIE)

    if not code or not state or not cookie_state or not secrets.compare_digest(state, cookie_state) or not verifier:
        await record_sign_in_event(
            db, result=SignInResult.failure, auth_method=AuthMethod.entra,
            failure_reason="invalid_state", ip_address=ip_address, user_agent=user_agent,
        )
        await db.commit()
        return RedirectResponse("/login?error=invalid_state", status_code=302)

    try:
        tokens = await entra_oidc.exchange_code_for_tokens(
            code=code, code_verifier=verifier, redirect_uri=settings.entra_sso_redirect_uri
        )
        claims = await entra_oidc.validate_id_token(tokens["id_token"])
    except (entra_oidc.TokenValidationError, KeyError):
        await record_sign_in_event(
            db, result=SignInResult.failure, auth_method=AuthMethod.entra,
            failure_reason="token_invalid", ip_address=ip_address, user_agent=user_agent,
        )
        await db.commit()
        return RedirectResponse("/login?error=token_invalid", status_code=302)

    tenant_id = claims["tid"]
    object_id = claims["oid"]
    email = claims.get("preferred_username") or claims.get("email") or ""
    display_name = claims.get("name")

    result = await db.execute(select(Organization).where(Organization.entra_tenant_id == tenant_id))
    org = result.scalar_one_or_none()
    if org is None:
        # Deliberate: orgs are provisioned by a platform admin ahead of time,
        # never auto-created just because some Entra tenant signed in.
        await record_sign_in_event(
            db, result=SignInResult.failure, auth_method=AuthMethod.entra,
            attempted_email=email, failure_reason="organization_not_provisioned",
            ip_address=ip_address, user_agent=user_agent,
        )
        await db.commit()
        return RedirectResponse("/login?error=organization_not_provisioned", status_code=302)

    # Moved up from after the suspended check below, so that branch gets
    # correct RLS context for free (real org.id already known here).
    await set_org_context(db, org.id)
    if org.status == OrganizationStatus.suspended:
        await record_sign_in_event(
            db, result=SignInResult.failure, auth_method=AuthMethod.entra,
            organization_id=org.id, attempted_email=email, failure_reason="organization_suspended",
            ip_address=ip_address, user_agent=user_agent,
        )
        await db.commit()
        return RedirectResponse("/login?error=organization_suspended", status_code=302)

    result = await db.execute(
        select(User).where(User.organization_id == org.id, User.entra_object_id == object_id)
    )
    user = result.scalar_one_or_none()

    if user is None:
        count_result = await db.execute(
            select(func.count()).select_from(User).where(User.organization_id == org.id)
        )
        is_first_user = count_result.scalar_one() == 0
        user = User(
            organization_id=org.id,
            entra_object_id=object_id,
            email=email,
            display_name=display_name,
            role=UserRole.org_admin if is_first_user else UserRole.member,
        )
        db.add(user)
        await db.flush()
    else:
        user.email = email
        user.display_name = display_name

    user.last_login_at = datetime.now(timezone.utc)

    _, raw_token = await session_manager.create_user_session(
        db,
        user_id=user.id,
        organization_id=org.id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    await record_sign_in_event(
        db, result=SignInResult.success, auth_method=AuthMethod.entra,
        organization_id=org.id, user_id=user.id, attempted_email=email,
        ip_address=ip_address, user_agent=user_agent,
    )
    await db.commit()

    response = RedirectResponse("/", status_code=302)
    response.delete_cookie(_OAUTH_STATE_COOKIE, path="/")
    response.delete_cookie(_OAUTH_VERIFIER_COOKIE, path="/")
    response.set_cookie(
        settings.session_cookie_name,
        raw_token,
        max_age=settings.session_idle_timeout_hours * 3600,
        **cookie_kwargs(),
    )
    return response


@router.post("/logout")
async def logout(request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    raw_token = request.cookies.get(settings.session_cookie_name)
    if raw_token:
        session = await session_manager.get_active_user_session(db, raw_token)
        if session is not None:
            session_manager.revoke_session(session)
            await db.commit()
    response = Response(status_code=204)
    response.delete_cookie(settings.session_cookie_name, path="/")
    return response


@router.get("/me")
async def me(user: User = Depends(get_current_user)) -> dict:
    return {
        "id": str(user.id),
        "email": user.email,
        "display_name": user.display_name,
        "role": user.role.value,
        "organization_id": str(user.organization_id),
    }


# ---------- Local email+password+TOTP login ----------
# For organizations with no entra_tenant_id set — otherwise their users
# would simply be locked out, since the Entra callback above only ever
# matches an org by tid claim. Two-step: password proves identity and
# opens a short-lived "pending" window (the same one both a returning
# user's OTP check and a brand-new user's OTP enrollment read from —
# mandatory TOTP either way, so a leaked password alone never reaches a
# real session), then either verify-otp or enroll-otp/confirm issues one.


class LocalLoginRequest(BaseModel):
    email: str
    password: str


class VerifyOtpRequest(BaseModel):
    code: str


class SetPasswordRequest(BaseModel):
    token: str
    new_password: str


class EnrollOtpConfirmRequest(BaseModel):
    secret: str
    code: str


async def _set_mfa_pending(db: AsyncSession, response: Response, user_id) -> None:
    raw_token, token_hash = new_opaque_token()
    now = datetime.now(timezone.utc)
    db.add(
        MfaPendingChallenge(
            user_id=user_id,
            token_hash=token_hash,
            created_at=now,
            expires_at=now + timedelta(minutes=settings.mfa_pending_timeout_minutes),
        )
    )
    short_lived = {**cookie_kwargs(), "max_age": settings.mfa_pending_timeout_minutes * 60}
    response.set_cookie(_MFA_PENDING_COOKIE, raw_token, **short_lived)


async def _get_pending_user(request: Request, db: AsyncSession) -> tuple[User, MfaPendingChallenge]:
    raw_token = request.cookies.get(_MFA_PENDING_COOKIE)
    if not raw_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no pending login")
    token_hash = hash_token(raw_token)
    result = await db.execute(select(MfaPendingChallenge).where(MfaPendingChallenge.token_hash == token_hash))
    challenge = result.scalar_one_or_none()
    if challenge is None or challenge.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "pending login expired — sign in again")
    # users is RLS-protected and there's no org context yet at this point in
    # the flow (that's precisely what this lookup is working out) — same
    # bypass the Entra callback effectively sidesteps by resolving org
    # first; here there's no org to resolve to ahead of time, so this reads
    # across all orgs the same way the worker's cross-org sweeps do.
    await set_platform_admin_context(db, is_admin=True)
    user = await db.get(User, challenge.user_id)
    if user is None or user.status != UserStatus.active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no pending login")
    return user, challenge


@router.post("/local-login", dependencies=[Depends(rate_limiter(login_limiter))])
async def local_login(body: LocalLoginRequest, request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    ip_address, user_agent = client_network_info(request)
    await set_platform_admin_context(db, is_admin=True)
    result = await db.execute(
        select(User).where(func.lower(User.email) == body.email.strip().lower(), User.auth_method == AuthMethod.local)
    )
    user = result.scalar_one_or_none()
    if user is None:
        # Spend the same Argon2 cost as a real verify so response timing
        # doesn't reveal whether this email exists (user enumeration).
        dummy_verify()
        # No user matched at all — nothing to attribute this to beyond the
        # attempted email itself, so organization_id stays unset.
        await record_sign_in_event(
            db, result=SignInResult.failure, auth_method=AuthMethod.local,
            attempted_email=body.email, failure_reason="invalid_credentials",
            ip_address=ip_address, user_agent=user_agent,
        )
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

    password_ok = user.password_hash is not None and verify_password(body.password, user.password_hash)
    if user.password_hash is None:
        # No local password set yet — still burn a verify so this branch is
        # indistinguishable, timing-wise, from a wrong password for an
        # enrolled user (the status check below short-circuits otherwise).
        dummy_verify()
    if not password_ok or user.status != UserStatus.active:
        # Same generic 401 as the "no user" case above — deliberately not
        # distinguishing the response, only the logged detail, to avoid
        # user enumeration.
        await record_sign_in_event(
            db, result=SignInResult.failure, auth_method=AuthMethod.local,
            organization_id=user.organization_id, user_id=user.id, attempted_email=body.email,
            failure_reason="invalid_credentials", ip_address=ip_address, user_agent=user_agent,
        )
        await db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")

    org = await db.get(Organization, user.organization_id)
    if org is None or org.status == OrganizationStatus.suspended:
        await record_sign_in_event(
            db, result=SignInResult.failure, auth_method=AuthMethod.local,
            organization_id=user.organization_id, user_id=user.id, attempted_email=body.email,
            failure_reason="organization_suspended", ip_address=ip_address, user_agent=user_agent,
        )
        await db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "organization access suspended")

    # A published public demo login has no meaningful TOTP secret to keep
    # private in the first place — go straight to a real session instead of
    # the normal MFA-pending/enroll-otp dance, which would otherwise mean
    # publishing a rotating 6-digit code alongside the password (or forcing
    # every visitor through their own enrollment just to look around).
    if org.is_demo_read_only:
        _, raw_session_token = await session_manager.create_user_session(
            db,
            user_id=user.id,
            organization_id=user.organization_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        user.last_login_at = datetime.now(timezone.utc)
        await record_sign_in_event(
            db, result=SignInResult.success, auth_method=AuthMethod.local,
            organization_id=user.organization_id, user_id=user.id, attempted_email=body.email,
            ip_address=ip_address, user_agent=user_agent,
        )
        await db.commit()
        response = Response(status_code=204)
        response.set_cookie(
            settings.session_cookie_name,
            raw_session_token,
            max_age=settings.session_idle_timeout_hours * 3600,
            **cookie_kwargs(),
        )
        return response

    response = JSONResponse({"needs_enrollment": user.otp_enrolled_at is None})
    await _set_mfa_pending(db, response, user.id)
    await db.commit()
    return response


@router.post("/verify-otp", dependencies=[Depends(rate_limiter(otp_limiter))])
async def verify_otp(body: VerifyOtpRequest, request: Request, db: AsyncSession = Depends(get_db)) -> Response:
    ip_address, user_agent = client_network_info(request)
    try:
        user, challenge = await _get_pending_user(request, db)
    except HTTPException:
        # Covers all 3 of _get_pending_user's internal raise points (missing
        # cookie, expired challenge, gone/inactive user) as one bucket —
        # equally unattributable to a specific org/user.
        await record_sign_in_event(
            db, result=SignInResult.failure, auth_method=AuthMethod.local,
            failure_reason="no_pending_challenge", ip_address=ip_address, user_agent=user_agent,
        )
        await db.commit()
        raise

    if user.otp_enrolled_at is None:
        await record_sign_in_event(
            db, result=SignInResult.failure, auth_method=AuthMethod.local,
            organization_id=user.organization_id, user_id=user.id, attempted_email=user.email,
            failure_reason="totp_not_enrolled", ip_address=ip_address, user_agent=user_agent,
        )
        await db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "TOTP not enrolled yet")

    code = body.code.strip()
    if not totp.verify_code(user.otp_secret, code):
        code_hash = totp.hash_recovery_code_for_lookup(code)
        result = await db.execute(
            select(UserRecoveryCode).where(
                UserRecoveryCode.user_id == user.id,
                UserRecoveryCode.code_hash == code_hash,
                UserRecoveryCode.used_at.is_(None),
            )
        )
        recovery = result.scalar_one_or_none()
        if recovery is None:
            await record_sign_in_event(
                db, result=SignInResult.failure, auth_method=AuthMethod.local,
                organization_id=user.organization_id, user_id=user.id, attempted_email=user.email,
                failure_reason="invalid_totp_or_recovery_code", ip_address=ip_address, user_agent=user_agent,
            )
            await db.commit()
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid code")
        recovery.used_at = datetime.now(timezone.utc)

    _, raw_session_token = await session_manager.create_user_session(
        db,
        user_id=user.id,
        organization_id=user.organization_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    user.last_login_at = datetime.now(timezone.utc)
    await record_sign_in_event(
        db, result=SignInResult.success, auth_method=AuthMethod.local,
        organization_id=user.organization_id, user_id=user.id, attempted_email=user.email,
        ip_address=ip_address, user_agent=user_agent,
    )
    await db.delete(challenge)
    await db.commit()

    response = Response(status_code=204)
    response.delete_cookie(_MFA_PENDING_COOKIE, path="/")
    response.set_cookie(
        settings.session_cookie_name,
        raw_session_token,
        max_age=settings.session_idle_timeout_hours * 3600,
        **cookie_kwargs(),
    )
    return response


@router.post("/set-password")
async def set_password(body: SetPasswordRequest, db: AsyncSession = Depends(get_db)) -> JSONResponse:
    token_hash = hash_token(body.token)
    result = await db.execute(select(PasswordSetupToken).where(PasswordSetupToken.token_hash == token_hash))
    setup_token = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if setup_token is None or setup_token.used_at is not None or setup_token.expires_at <= now:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this link is invalid or has expired")

    # See _get_pending_user's comment — same pre-org-context bypass, needed
    # here because users is RLS-protected and this endpoint doesn't know
    # the org until after this lookup.
    await set_platform_admin_context(db, is_admin=True)
    user = await db.get(User, setup_token.user_id)
    if user is None or user.status != UserStatus.active:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "this link is invalid or has expired")
    if len(body.new_password) < 12:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "password must be at least 12 characters")

    user.password_hash = hash_password(body.new_password)
    setup_token.used_at = now

    response = JSONResponse({"needs_enrollment": True})
    await _set_mfa_pending(db, response, user.id)
    await db.commit()
    return response


@router.post("/enroll-otp")
async def enroll_otp(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    user, _challenge = await _get_pending_user(request, db)
    secret = totp.generate_secret()
    uri = totp.provisioning_uri(secret, user.email)
    return {"secret": secret, "qr_code_data_uri": totp.qr_code_data_uri(uri)}


@router.post("/enroll-otp/confirm")
async def enroll_otp_confirm(
    body: EnrollOtpConfirmRequest, request: Request, db: AsyncSession = Depends(get_db)
) -> JSONResponse:
    ip_address, user_agent = client_network_info(request)
    try:
        user, challenge = await _get_pending_user(request, db)
    except HTTPException:
        await record_sign_in_event(
            db, result=SignInResult.failure, auth_method=AuthMethod.local,
            failure_reason="no_pending_challenge", ip_address=ip_address, user_agent=user_agent,
        )
        await db.commit()
        raise

    if not totp.verify_code(body.secret, body.code):
        await record_sign_in_event(
            db, result=SignInResult.failure, auth_method=AuthMethod.local,
            organization_id=user.organization_id, user_id=user.id, attempted_email=user.email,
            failure_reason="invalid_totp_enrollment_code", ip_address=ip_address, user_agent=user_agent,
        )
        await db.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "code didn't match — check your authenticator app and try again")

    now = datetime.now(timezone.utc)
    user.otp_secret = body.secret
    user.otp_enrolled_at = now

    codes = totp.generate_recovery_codes()
    for plaintext, code_hash in codes:
        db.add(UserRecoveryCode(user_id=user.id, code_hash=code_hash, created_at=now))

    _, raw_session_token = await session_manager.create_user_session(
        db,
        user_id=user.id,
        organization_id=user.organization_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    user.last_login_at = now
    await record_sign_in_event(
        db, result=SignInResult.success, auth_method=AuthMethod.local,
        organization_id=user.organization_id, user_id=user.id, attempted_email=user.email,
        ip_address=ip_address, user_agent=user_agent,
    )
    await db.delete(challenge)
    await db.commit()

    response = JSONResponse({"recovery_codes": [plaintext for plaintext, _hash in codes]})
    response.delete_cookie(_MFA_PENDING_COOKIE, path="/")
    response.set_cookie(
        settings.session_cookie_name,
        raw_session_token,
        max_age=settings.session_idle_timeout_hours * 3600,
        **cookie_kwargs(),
    )
    return response
