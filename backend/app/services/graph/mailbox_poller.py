"""Graph delta-query polling for a shared mailbox's Inbox. Deliberately
independent of parsedmarc's own MSGraphConnection/get_dmarc_reports_from_mailbox
(see app/services/ingestion/parsedmarc_adapter.py docstring for why): that
path needs Mail.ReadWrite (it creates folders and moves/deletes messages)
and has no delta/incremental support, re-listing the whole mailbox every
call. Our Entra "Mail Access" app is deliberately Mail.Read-only, and the
mailbox_connections.delta_link column exists specifically so polling only
ever fetches messages that arrived since the last successful run."""

import asyncio
import logging

import httpx

from app.services.graph.client_credentials import get_graph_token

logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_MAX_THROTTLE_RETRIES = 3


class GraphApiError(Exception):
    pass


async def _graph_get(client: httpx.AsyncClient, url: str, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    for attempt in range(_MAX_THROTTLE_RETRIES + 1):
        resp = await client.get(url, headers=headers)
        if resp.status_code == 429 and attempt < _MAX_THROTTLE_RETRIES:
            retry_after = float(resp.headers.get("Retry-After", "5"))
            logger.warning("Graph throttled (429), retrying in %ss", retry_after)
            await asyncio.sleep(retry_after)
            continue
        resp.raise_for_status()
        return resp.json()
    raise GraphApiError("Graph API throttled past max retries")


async def fetch_new_message_ids(
    *, tenant_id: str, mailbox: str, delta_link: str | None
) -> tuple[list[str], str | None]:
    """Returns (new_message_ids, new_delta_link) — the delta_link is what
    gets persisted to mailbox_connections.delta_link for next time. On the
    very first sync (delta_link is None), Graph's delta query returns every
    message currently in Inbox, which is the desired behavior (ingest the
    existing backlog, not just messages arriving from here on)."""
    token = await get_graph_token(tenant_id)
    url = delta_link or (
        f"{GRAPH_BASE}/users/{mailbox}/mailFolders/Inbox/messages/delta?$select=id"
    )

    message_ids: list[str] = []
    async with httpx.AsyncClient(timeout=30) as client:
        while True:
            data = await _graph_get(client, url, token)
            for item in data.get("value", []):
                if "@removed" in item:
                    continue
                message_ids.append(item["id"])

            if "@odata.nextLink" in data:
                url = data["@odata.nextLink"]
                continue

            return message_ids, data.get("@odata.deltaLink", delta_link)


async def fetch_message_raw_mime(*, tenant_id: str, mailbox: str, message_id: str) -> bytes:
    """Raw RFC 822 (.eml) bytes for a message, via Graph's $value export —
    what parsedmarc's parser expects as input."""
    token = await get_graph_token(tenant_id)
    url = f"{GRAPH_BASE}/users/{mailbox}/messages/{message_id}/$value"
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        resp.raise_for_status()
        return resp.content
