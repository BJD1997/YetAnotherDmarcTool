import hashlib
import secrets


def new_opaque_token() -> tuple[str, str]:
    """(raw, sha256_hash) — the raw value is what goes in a cookie/link and
    is never stored; only the hash is persisted, same pattern
    session_manager.py uses for session tokens."""
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
