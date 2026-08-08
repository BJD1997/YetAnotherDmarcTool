import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.platform_admin_session import PlatformAdminSession
from app.models.session import UserSession

_IDLE_TIMEOUT = timedelta(hours=settings.session_idle_timeout_hours)
_ABSOLUTE_TIMEOUT = timedelta(days=settings.session_absolute_timeout_days)


def _new_raw_token() -> str:
    return secrets.token_urlsafe(32)


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_user_session(
    db: AsyncSession,
    *,
    user_id,
    organization_id,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[UserSession, str]:
    raw_token = _new_raw_token()
    now = _now()
    session = UserSession(
        user_id=user_id,
        organization_id=organization_id,
        session_token_hash=_hash_token(raw_token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + _IDLE_TIMEOUT,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(session)
    await db.flush()
    return session, raw_token


async def get_active_user_session(db: AsyncSession, raw_token: str) -> UserSession | None:
    token_hash = _hash_token(raw_token)
    result = await db.execute(select(UserSession).where(UserSession.session_token_hash == token_hash))
    session = result.scalar_one_or_none()
    if session is None:
        return None
    return _validate_and_refresh(session)


async def create_platform_admin_session(
    db: AsyncSession,
    *,
    platform_admin_id,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[PlatformAdminSession, str]:
    raw_token = _new_raw_token()
    now = _now()
    session = PlatformAdminSession(
        platform_admin_id=platform_admin_id,
        session_token_hash=_hash_token(raw_token),
        created_at=now,
        last_seen_at=now,
        expires_at=now + _IDLE_TIMEOUT,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(session)
    await db.flush()
    return session, raw_token


async def get_active_platform_admin_session(
    db: AsyncSession, raw_token: str
) -> PlatformAdminSession | None:
    token_hash = _hash_token(raw_token)
    result = await db.execute(
        select(PlatformAdminSession).where(PlatformAdminSession.session_token_hash == token_hash)
    )
    session = result.scalar_one_or_none()
    if session is None:
        return None
    return _validate_and_refresh(session)


def _validate_and_refresh(session):
    now = _now()
    if session.revoked_at is not None:
        return None
    if session.expires_at <= now:
        return None
    if now - session.created_at > _ABSOLUTE_TIMEOUT:
        return None
    session.last_seen_at = now
    session.expires_at = min(now + _IDLE_TIMEOUT, session.created_at + _ABSOLUTE_TIMEOUT)
    return session


def revoke_session(session) -> None:
    session.revoked_at = _now()


def cookie_kwargs() -> dict:
    # `secure` must be True whenever this app is actually reached over HTTPS
    # (i.e. in every real deployment, behind NPM). It's derived from
    # PUBLIC_BASE_URL rather than hardcoded so local `http://localhost` smoke
    # testing (no TLS) still works without a separate dev-only code path.
    return {
        "httponly": True,
        "secure": settings.public_base_url.startswith("https://"),
        "samesite": "lax",
        "path": "/",
    }
