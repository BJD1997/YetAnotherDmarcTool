import pytest
from cryptography.fernet import Fernet

from app.config import settings
from app.services.crypto import secrets as crypto_secrets


@pytest.fixture()
def fernet_key(monkeypatch):
    """A real Fernet key wired into settings, with the lru_cache cleared so the
    helper picks it up (and cleared again after, so nothing leaks between tests)."""
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "fernet_key", key)
    crypto_secrets._fernet.cache_clear()
    yield key
    crypto_secrets._fernet.cache_clear()


def test_encrypted_secret_roundtrip(fernet_key):
    from app.services.auth.totp_secret import EncryptedSecret

    col = EncryptedSecret()
    plaintext = "JBSWY3DPEHPK3PXP"

    stored = col.process_bind_param(plaintext, None)
    assert stored is not None
    assert stored != plaintext                     # actually encrypted at rest
    assert col.process_result_value(stored, None) == plaintext

    assert col.process_bind_param(None, None) is None
    assert col.process_result_value(None, None) is None


def test_encrypted_secret_reads_legacy_plaintext(fernet_key):
    """A pre-migration plaintext base32 secret must still verify (it isn't a
    valid Fernet token, so decrypt falls back to returning it unchanged)."""
    from app.services.auth.totp_secret import EncryptedSecret

    col = EncryptedSecret()
    legacy_plaintext = "JBSWY3DPEHPK3PXP"
    assert col.process_result_value(legacy_plaintext, None) == legacy_plaintext
