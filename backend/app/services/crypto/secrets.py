from functools import lru_cache

from cryptography.fernet import Fernet, MultiFernet

from app.config import settings


@lru_cache(maxsize=1)
def _fernet() -> MultiFernet:
    # Comma-separated, newest first: encrypts with the first key, decrypts
    # with any of them — so a key can be rotated without orphaning every
    # secret written under the previous one.
    keys = [key.strip() for key in (settings.fernet_key or "").split(",") if key.strip()]
    if not keys:
        raise RuntimeError("FERNET_KEY is not set — required to encrypt/decrypt TOTP secrets at rest.")
    return MultiFernet([Fernet(key) for key in keys])


def encrypt_secret(plaintext: str) -> bytes:
    return _fernet().encrypt(plaintext.encode("utf-8"))


def decrypt_secret(ciphertext: bytes) -> str:
    return _fernet().decrypt(ciphertext).decode("utf-8")
