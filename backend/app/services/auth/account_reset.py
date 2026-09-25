"""Credential resets for local-auth users, shared by the self-service
endpoints (app/routers/auth.py) and org-admin resets (app/routers/users.py).
None of the tables touched here besides `users` are RLS-scoped (they're read
before any org context exists — see each model), so these work under either
an org or a platform-admin context."""

from datetime import datetime, timedelta, timezone

from fastapi import Request
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.enums import SignInResult
from app.models.mfa_pending_challenge import MfaPendingChallenge
from app.models.password_setup_token import PasswordSetupToken
from app.models.user import User
from app.models.user_recovery_code import UserRecoveryCode
from app.services.auth import totp
from app.services.auth.sign_in_log import client_network_info, record_sign_in_event
from app.services.auth.tokens import new_opaque_token


async def issue_password_setup_link(db: AsyncSession, user: User) -> str:
    """Creates a one-time "set your password" link, voiding any earlier
    unused one so only the newest link works."""
    now = datetime.now(timezone.utc)
    await db.execute(
        update(PasswordSetupToken)
        .where(PasswordSetupToken.user_id == user.id, PasswordSetupToken.used_at.is_(None))
        .values(used_at=now)
    )
    raw_token, token_hash = new_opaque_token()
    db.add(
        PasswordSetupToken(
            user_id=user.id,
            token_hash=token_hash,
            created_at=now,
            expires_at=now + timedelta(hours=settings.password_setup_token_timeout_hours),
        )
    )
    return f"{settings.public_base_url}/set-password?token={raw_token}"


async def cancel_pending_logins(db: AsyncSession, user: User) -> None:
    """Drops half-finished logins (password checked, MFA step pending) so a
    reset can't be raced by someone already past the password step."""
    await db.execute(delete(MfaPendingChallenge).where(MfaPendingChallenge.user_id == user.id))


async def replace_recovery_codes(db: AsyncSession, user: User) -> list[str]:
    now = datetime.now(timezone.utc)
    await db.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id))
    codes = totp.generate_recovery_codes()
    for _plaintext, code_hash in codes:
        db.add(UserRecoveryCode(user_id=user.id, code_hash=code_hash, created_at=now))
    return [plaintext for plaintext, _hash in codes]


async def clear_mfa(db: AsyncSession, user: User) -> None:
    """Removes the user's authenticator and recovery codes — their next
    sign-in goes through first-time enrollment again."""
    user.otp_secret = None
    user.otp_enrolled_at = None
    await db.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id))
    await cancel_pending_logins(db, user)


async def log_account_change(
    db: AsyncSession, request: Request, user: User, action: str, *, actor_email: str | None = None
) -> None:
    """Records a password/MFA change in the org's Sign-in activity log.
    actor_email is whoever made it when that wasn't the user themselves.
    Call it last before commit: it switches the transaction to the
    platform-admin RLS context (see record_sign_in_event)."""
    ip_address, user_agent = client_network_info(request)
    await record_sign_in_event(
        db, result=SignInResult.account_change, auth_method=user.auth_method,
        organization_id=user.organization_id, user_id=user.id, attempted_email=user.email,
        failure_reason=action, actor_email=actor_email, ip_address=ip_address, user_agent=user_agent,
    )
