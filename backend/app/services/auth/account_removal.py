"""Which accounts can be removed: only ones nobody can sign in with any
more. Removing deletes the user row; sessions, recovery codes and setup
links go with it, while the sign-in log, sender reviews and notifications
keep their history with the link to the account cleared."""

from app.models.enums import AuthMethod, UserStatus
from app.models.organization import Organization
from app.models.user import User

_PROVIDER_METHOD = {"microsoft": AuthMethod.entra, "google": AuthMethod.google, "oidc": AuthMethod.oidc}


def removal_blocker(org: Organization, user: User) -> str | None:
    """Why `user` can't be removed, or None when it can. An account of the
    current provider is never removable, even disabled: its owner would
    just sign in again as a new account, while disabling keeps them out."""
    if user.is_break_glass:
        return "the break-glass account can't be removed; make another account the break-glass account first"
    if user.auth_method == _PROVIDER_METHOD.get(org.sign_in_provider):
        return "this account signs in through your provider: disable it instead, which keeps them out"
    if user.auth_method == AuthMethod.local and user.status == UserStatus.active:
        return "this account can still sign in: disable it first"
    return None
