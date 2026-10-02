from functools import lru_cache

from cryptography.fernet import Fernet, MultiFernet

from app.config import settings

_HOW_TO_FIX = (
    "Generate one with: openssl rand -base64 32 | tr '+/' '-_' — then set it as FERNET_KEY in the "
    "environment (the .env file, or the stack's environment variables in Portainer) and restart or "
    "redeploy. Keep it safe: two-factor secrets saved under it can't be read without it."
)


class SecretKeyNotConfigured(RuntimeError):
    """FERNET_KEY is missing or unusable. Shown to the user as is (503), so
    whoever is setting the server up sees what to fix instead of a 500."""


def fernet_key_problem() -> str | None:
    """Why FERNET_KEY can't be used, or None when it's fine."""
    keys = [key.strip() for key in (settings.fernet_key or "").split(",") if key.strip()]
    if not keys:
        return "FERNET_KEY is not set. It encrypts two-factor secrets, so sign-in setup can't finish. " + _HOW_TO_FIX
    for key in keys:
        try:
            Fernet(key)
        except (ValueError, TypeError):
            return "FERNET_KEY isn't a valid key (it must be 32 random bytes, url-safe base64). " + _HOW_TO_FIX
    return None


@lru_cache(maxsize=1)
def _fernet() -> MultiFernet:
    # Comma-separated, newest first: encrypts with the first key, decrypts
    # with any of them — so a key can be rotated without orphaning every
    # secret written under the previous one.
    problem = fernet_key_problem()
    if problem:
        raise SecretKeyNotConfigured(problem)
    keys = [key.strip() for key in (settings.fernet_key or "").split(",") if key.strip()]
    return MultiFernet([Fernet(key) for key in keys])


def encrypt_secret(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode("utf-8")
