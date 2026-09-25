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


def test_rotated_key_still_decrypts_secrets_written_with_the_old_key(fernet_key, monkeypatch):
    """Rotation: FERNET_KEY="<new>,<old>" encrypts with the new key but still
    decrypts anything written under the old one."""
    from app.services.auth.totp_secret import EncryptedSecret

    col = EncryptedSecret()
    stored_under_old_key = col.process_bind_param("JBSWY3DPEHPK3PXP", None)

    new_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "fernet_key", f"{new_key},{fernet_key}")
    crypto_secrets._fernet.cache_clear()

    assert col.process_result_value(stored_under_old_key, None) == "JBSWY3DPEHPK3PXP"
    rewritten = col.process_bind_param("JBSWY3DPEHPK3PXP", None)
    assert Fernet(new_key.encode()).decrypt(rewritten.encode()) == b"JBSWY3DPEHPK3PXP"


def test_undecryptable_ciphertext_is_not_passed_off_as_a_secret(fernet_key, monkeypatch, caplog):
    """If the key that wrote a secret is gone, the ciphertext must not be
    returned as though it were a legacy plaintext secret — pyotp can't use it
    and would error out of verify-otp before recovery codes are even tried."""
    from app.services.auth import totp
    from app.services.auth.totp_secret import EncryptedSecret

    col = EncryptedSecret()
    stored = col.process_bind_param("JBSWY3DPEHPK3PXP", None)
    monkeypatch.setattr(settings, "fernet_key", Fernet.generate_key().decode())
    crypto_secrets._fernet.cache_clear()

    secret = col.process_result_value(stored, None)

    assert secret is None
    assert "FERNET_KEY" in caplog.text
    assert totp.verify_code(secret, "123456") is False
