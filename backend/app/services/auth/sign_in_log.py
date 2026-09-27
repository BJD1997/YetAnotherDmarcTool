"""Writes SignInEvent rows for every completed sign-in attempt across
auth.py's three flows. record_sign_in_event unconditionally bypasses RLS
(set_platform_admin_context) immediately before its insert rather than
trying to rely on whatever org context a given call site happens to already
have set — that state is genuinely inconsistent across auth.py's 12 write
points (some run before any org is known, some after, some under an
existing bypass for unrelated reasons), and in every one of them this
insert is the last DB operation before that request's own commit, so
widening the context here can't leak anything to a later read in the same
request."""

import ipaddress
from datetime import datetime, timezone
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.rls import set_platform_admin_context
from app.models.enums import AuthMethod, SignInResult
from app.models.sign_in_event import SignInEvent


def _mask_ip(ip: str | None) -> str | None:
    """Drop the host portion so the sign-in log shows the rough network, not
    the exact address — for a public demo (settings.mask_sign_in_ips). Keeps
    the first two labels: 203.0.113.10 -> 203.0.x.x, 2606:4700::1 -> 2606:4700:x."""
    if not ip:
        return ip
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "masked"
    if addr.version == 4:
        a, b = str(addr).split(".")[:2]
        return f"{a}.{b}.x.x"
    groups = addr.exploded.split(":")
    return f"{groups[0]}:{groups[1]}:x"


def client_network_info(request: Request) -> tuple[str | None, str | None]:
    """(ip_address, user_agent). The IP is request.client.host — the address
    uvicorn resolved from X-Forwarded-For through the proxies listed in
    FORWARDED_ALLOW_IPS (NPM behind Cloudflare, or Azure Container Apps'
    ingress), the same one the rate limiter keys on.

    Deliberately not Cloudflare's CF-Connecting-IP header: it's only
    trustworthy when every request provably passes through Cloudflare, and
    on a deployment without Cloudflare in front (Azure, or a host reachable
    directly) any client can send its own, putting a made-up address in the
    sign-in log."""
    ip_address = request.client.host if request.client else None
    return ip_address, request.headers.get("user-agent")


async def record_sign_in_event(
    db: AsyncSession,
    *,
    result: SignInResult,
    auth_method: AuthMethod,
    ip_address: str | None,
    user_agent: str | None,
    organization_id: UUID | None = None,
    user_id: UUID | None = None,
    attempted_email: str | None = None,
    failure_reason: str | None = None,
    actor_email: str | None = None,
    created_at: datetime | None = None,
) -> None:
    await set_platform_admin_context(db, is_admin=True)
    if settings.mask_sign_in_ips:
        ip_address = _mask_ip(ip_address)
    db.add(
        SignInEvent(
            organization_id=organization_id,
            user_id=user_id,
            attempted_email=attempted_email,
            auth_method=auth_method,
            result=result,
            failure_reason=failure_reason,
            actor_email=actor_email,
            ip_address=ip_address,
            user_agent=user_agent,
            created_at=created_at or datetime.now(timezone.utc),
        )
    )
