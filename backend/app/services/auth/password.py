from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

_hasher = PasswordHasher()

# A throwaway but valid Argon2 hash, used to spend the same CPU on a login
# attempt for a non-existent (or password-less) account as for a real one, so
# response timing can't be used to tell whether an email exists (user
# enumeration). The salt is random per process start; verify cost depends on
# the parameters, not the specific hash, so this matches a real verify.
_DUMMY_HASH = _hasher.hash("timing-equalizer-not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def dummy_verify() -> None:
    """Constant-work no-op verification. Call it on the user-not-found /
    no-password-set login branches so they take as long as a real
    verify_password() would, removing the timing side channel."""
    try:
        _hasher.verify(_DUMMY_HASH, "timing-equalizer-wrong-guess")
    except VerifyMismatchError:
        pass
