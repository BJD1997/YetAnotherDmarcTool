"""SQLAlchemy column type that transparently encrypts TOTP secrets at rest
with the app's Fernet key (see app/services/crypto/secrets.py). Applied to
User.otp_secret and PlatformAdmin.otp_secret so every read/write path
(enrollment, verification) encrypts/decrypts without each call site having to
remember to.

Reads fall back to returning the stored value unchanged if it isn't a valid
Fernet token, so a legacy plaintext secret keeps verifying — both for existing
enrollments during the transition and for the brief window before the 0020
migration's backfill re-encrypts them in place. Requires FERNET_KEY to be set
for any *write* (new enrollment); that's now a hard requirement for local-auth
TOTP, enforced fail-closed by crypto.secrets._fernet().
"""

import logging

from cryptography.fernet import InvalidToken
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from app.services.crypto.secrets import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)


class EncryptedSecret(TypeDecorator):
    impl = String
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return encrypt_secret(value).decode("ascii")

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        try:
            return decrypt_secret(value.encode("ascii"))
        except (InvalidToken, ValueError):
            # Legacy plaintext secret not yet migrated to ciphertext — return
            # as-is so TOTP still verifies. The 0020 migration backfills these.
            logger.warning("TOTP secret read as legacy plaintext (not yet encrypted at rest)")
            return value
