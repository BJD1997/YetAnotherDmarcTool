"""App-only (client-credentials) Graph token acquisition for the "Mail
Access" Entra app — one global client_id/client_secret (operator-owned,
multi-tenant app registration) reused across every org's tenant, per the
admin-consent + Exchange Application Access Policy pattern in the plan.
MSAL's ConfidentialClientApplication is sync; calls are offloaded to a
thread so the event loop isn't blocked (MSAL also handles its own token
caching/expiry internally, so this is cheap on cache hits)."""

import asyncio

import msal

from app.config import settings

GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]

_apps: dict[str, msal.ConfidentialClientApplication] = {}


def _get_app(tenant_id: str) -> msal.ConfidentialClientApplication:
    app = _apps.get(tenant_id)
    if app is None:
        app = msal.ConfidentialClientApplication(
            client_id=settings.entra_mail_client_id,
            client_credential=settings.entra_mail_client_secret,
            authority=f"https://login.microsoftonline.com/{tenant_id}",
        )
        _apps[tenant_id] = app
    return app


class GraphAuthError(Exception):
    pass


async def get_graph_token(tenant_id: str) -> str:
    app = _get_app(tenant_id)
    result = await asyncio.to_thread(app.acquire_token_for_client, scopes=GRAPH_SCOPE)
    if "access_token" not in result:
        raise GraphAuthError(
            f"failed to acquire Graph token for tenant {tenant_id}: "
            f"{result.get('error')}: {result.get('error_description')}"
        )
    return result["access_token"]
