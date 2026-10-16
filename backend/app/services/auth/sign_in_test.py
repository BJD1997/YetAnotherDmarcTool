"""Test sign-in for a provider before it's saved: the settings ride in an
encrypted cookie for ten minutes, and a passed test hands back a ticket
that only matches those exact settings (and, for Microsoft and Google,
carries the tenant or Workspace the provider's signed token named), so
saving can require it."""

import dataclasses
import hashlib
import json

from cryptography.fernet import InvalidToken

from app.services.crypto.secrets import fernet

TTL_SECONDS = 600
COOKIE = "sign_in_test"


@dataclasses.dataclass(frozen=True)
class PendingSignIn:
    org_id: str
    provider: str  # microsoft | google | oidc
    # OIDC only:
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""


@dataclasses.dataclass(frozen=True)
class Passed:
    # What the provider's token said: the Microsoft tenant or Google Workspace.
    tenant_id: str | None = None
    workspace: str | None = None


def _digest(pending: PendingSignIn) -> str:
    return hashlib.sha256(json.dumps(dataclasses.asdict(pending), sort_keys=True).encode()).hexdigest()


def pending_cookie(pending: PendingSignIn) -> str:
    return fernet().encrypt(json.dumps(dataclasses.asdict(pending)).encode()).decode()


def read_pending(value: str | None) -> PendingSignIn | None:
    if not value:
        return None
    try:
        return PendingSignIn(**json.loads(fernet().decrypt(value.encode(), ttl=TTL_SECONDS)))
    except (InvalidToken, ValueError, TypeError):
        return None


def ticket_for(pending: PendingSignIn, passed: Passed) -> str:
    return fernet().encrypt(json.dumps({"passed": _digest(pending), **dataclasses.asdict(passed)}).encode()).decode()


def read_ticket(ticket: str, pending: PendingSignIn) -> Passed | None:
    """What the test established, if the ticket is for exactly these settings."""
    try:
        data = json.loads(fernet().decrypt(ticket.encode(), ttl=TTL_SECONDS))
    except (InvalidToken, ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("passed") != _digest(pending):
        return None
    return Passed(tenant_id=data.get("tenant_id"), workspace=data.get("workspace"))
