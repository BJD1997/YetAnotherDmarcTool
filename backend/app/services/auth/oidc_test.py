"""Test sign-in for OIDC settings that aren't saved yet: the settings ride
in an encrypted cookie for ten minutes, and a passed test hands back a
ticket that only matches those exact settings, so saving can require it."""

import dataclasses
import hashlib
import json

from cryptography.fernet import InvalidToken

from app.services.crypto.secrets import fernet

TTL_SECONDS = 600
COOKIE = "oidc_test"


@dataclasses.dataclass(frozen=True)
class PendingOidc:
    org_id: str
    issuer: str
    client_id: str
    client_secret: str


def _digest(pending: PendingOidc) -> str:
    return hashlib.sha256(json.dumps(dataclasses.asdict(pending), sort_keys=True).encode()).hexdigest()


def pending_cookie(pending: PendingOidc) -> str:
    return fernet().encrypt(json.dumps(dataclasses.asdict(pending)).encode()).decode()


def read_pending(value: str | None) -> PendingOidc | None:
    if not value:
        return None
    try:
        return PendingOidc(**json.loads(fernet().decrypt(value.encode(), ttl=TTL_SECONDS)))
    except (InvalidToken, ValueError, TypeError):
        return None


def ticket_for(pending: PendingOidc) -> str:
    return fernet().encrypt(json.dumps({"passed": _digest(pending)}).encode()).decode()


def ticket_matches(ticket: str, pending: PendingOidc) -> bool:
    try:
        data = json.loads(fernet().decrypt(ticket.encode(), ttl=TTL_SECONDS))
    except (InvalidToken, ValueError, TypeError):
        return False
    return isinstance(data, dict) and data.get("passed") == _digest(pending)
