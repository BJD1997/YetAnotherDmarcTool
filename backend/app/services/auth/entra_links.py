"""Admin-consent link generation for the two operator-owned Entra apps —
shared between the platform-admin router (handing links to a new customer)
and the org-scoped router (so a customer's own org_admin can find/re-find
these links from inside their own dashboard, e.g. to fix a lapsed consent
or hand the SSO link to their Global Admin later)."""

from urllib.parse import urlencode

from app.config import settings
from app.models.organization import Organization


def entra_consent_urls(org: Organization) -> dict | None:
    if org.entra_tenant_id is None or not settings.entra_mail_client_id or not settings.entra_sso_client_id:
        return None
    mail_redirect = f"{settings.public_base_url}/api/admin/entra/consent-callback"
    return {
        "mail_access_consent_url": (
            f"https://login.microsoftonline.com/{org.entra_tenant_id}/adminconsent?"
            + urlencode({"client_id": settings.entra_mail_client_id, "redirect_uri": mail_redirect})
        ),
        "sso_consent_url": (
            f"https://login.microsoftonline.com/{org.entra_tenant_id}/adminconsent?"
            + urlencode({"client_id": settings.entra_sso_client_id, "redirect_uri": settings.entra_sso_redirect_uri})
        ),
    }
