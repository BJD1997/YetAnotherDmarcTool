import base64
import hashlib
import secrets


def generate_verifier() -> str:
    return secrets.token_urlsafe(64)  # ~86 chars, within the 43-128 char RFC 7636 range


def derive_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
