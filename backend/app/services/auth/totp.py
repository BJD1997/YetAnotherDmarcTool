"""TOTP (RFC 6238) enrollment/verification + recovery codes for local-auth
users (see app/routers/auth.py). Recovery codes are high-entropy random
tokens, not user-chosen secrets, so they're hashed with the same
lookup-by-hash SHA-256 pattern session_manager.py already uses for session
tokens — not argon2 (which is for slow-hashing low-entropy passwords, and
would force an O(n) verify loop here since we don't know in advance which
of a user's several unused codes is being presented)."""

import base64
import datetime
import hashlib
import io
import secrets

import pyotp
import qrcode
import qrcode.image.svg

RECOVERY_CODE_COUNT = 10
_ISSUER = "YetAnotherDmarcTool"


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=_ISSUER)


def qr_code_data_uri(uri: str) -> str:
    img = qrcode.make(uri, image_factory=qrcode.image.svg.SvgImage)
    buf = io.BytesIO()
    img.save(buf)
    return f"data:image/svg+xml;base64,{base64.b64encode(buf.getvalue()).decode()}"


def verify_code(secret: str | None, code: str) -> bool:
    # None = a stored secret that couldn't be decrypted (see totp_secret.py);
    # fail the code so callers fall through to recovery codes.
    if secret is None:
        return False
    # valid_window=1 tolerates the presented code being one 30s step behind
    # or ahead, for ordinary clock drift between the server and the user's
    # authenticator app.
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def accept_code(secret: str | None, code: str, last_used_step: int | None) -> int | None:
    """Sign-in check: like verify_code, but each code works only once. Returns
    the 30-second time step the code belongs to (store it as the account's
    last used step), or None if the code is wrong or its step — or a later
    one — was already used. Without this, a code watched over someone's
    shoulder or phished stays valid for its whole ~90 s window."""
    if secret is None:
        return None
    totp = pyotp.TOTP(secret)
    now = datetime.datetime.now()
    for offset in (-1, 0, 1):
        if pyotp.utils.strings_equal(str(code), str(totp.at(now, offset))):
            step = totp.timecode(now) + offset
            if last_used_step is not None and step <= last_used_step:
                return None
            return step
    return None


def current_step(secret: str) -> int:
    """The time step of the current code — recorded at enrollment, so the
    code used to confirm a new authenticator can't then also sign in."""
    return pyotp.TOTP(secret).timecode(datetime.datetime.now())


def _hash_recovery_code(code: str) -> str:
    # Normalized the same way at generation and at lookup time — a user
    # re-typing a code with different case or stray whitespace must still
    # hash to the same stored value.
    normalized = code.strip().lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def generate_recovery_codes() -> list[tuple[str, str]]:
    """Returns [(plaintext, hash), ...] — plaintext is shown to the user
    exactly once by the caller and never stored; only the hash is
    persisted."""
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        plaintext = "-".join(secrets.token_hex(2) for _ in range(4))
        codes.append((plaintext, _hash_recovery_code(plaintext)))
    return codes


def hash_recovery_code_for_lookup(code: str) -> str:
    return _hash_recovery_code(code)
