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

# Real DMARC/TLS-RPT report emails are kilobytes to a few MB. The report
# mailboxes are public (the rua= address is in DNS), so anything bigger is
# skipped unread rather than held in memory: one huge message in the shared
# hosted mailbox would otherwise slow processing for every organization.
MAX_MESSAGE_BYTES = 25 * 1024 * 1024


class MessageTooLargeError(Exception):
    """The message is larger than MAX_MESSAGE_BYTES; it's skipped."""


class GraphApiError(Exception):
    """Graph refused a request. The message is shown to people as is, so it
    never contains the request URL (a delta link carries a sync token)."""


class GraphUnavailableError(GraphApiError):
    """Graph kept failing in a way that usually passes (throttling, 5xx,
    timeouts) after every retry. A poll stops on it rather than skipping the
    message, so the delta link isn't moved past anything unread."""


# Waits before each retry of a temporary failure. A Microsoft 365 outage
# (12/13 Nov 2026) showed up as 502s; a few short retries ride out a blip
# without holding a poll for minutes when the outage is real.
_RETRY_DELAYS = (2.0, 5.0, 15.0)
_MAX_RETRY_AFTER = 60.0
_TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


class _Transient(Exception):
    def __init__(self, response: httpx.Response):
        self.response = response
        self.reason = "throttled, HTTP 429" if response.status_code == 429 else f"HTTP {response.status_code}"


def _graph_error_detail(resp: httpx.Response) -> str:
    try:
        error = resp.json().get("error") or {}
    except ValueError:
        return ""
    if not isinstance(error, dict):
        return ""
    code, message = error.get("code"), error.get("message")
    return ": ".join(str(part) for part in (code, message) if part)[:300]


def _unavailable(reason: str) -> GraphUnavailableError:
    return GraphUnavailableError(
        f"Microsoft 365 didn't respond ({reason}), also not after {len(_RETRY_DELAYS)} automatic retries. "
        "Nothing was skipped: the next poll continues where this one stopped."
    )


def _refused(resp: httpx.Response) -> GraphApiError:
    detail = _graph_error_detail(resp)
    return GraphApiError(f"Microsoft 365 refused the request (HTTP {resp.status_code}{': ' + detail if detail else ''})")


def _retry_delay(attempt: int, resp: httpx.Response | None) -> float:
    if resp is not None and resp.status_code == 429:
        try:
            return min(max(float(resp.headers.get("Retry-After", "5")), 0.0), _MAX_RETRY_AFTER)
        except ValueError:
            pass
    return _RETRY_DELAYS[attempt]


async def _with_retries(request, url_kind: str):
    """Runs `request()` (which returns a value or raises) and retries it on
    throttling, 5xx and network errors. `request` raises _Transient for a
    retryable answer; any other exception goes straight through."""
    for attempt in range(len(_RETRY_DELAYS) + 1):
        try:
            return await request()
        except _Transient as exc:
            reason, resp = exc.reason, exc.response
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            reason, resp = ("timed out" if isinstance(exc, httpx.TimeoutException) else "connection failed"), None
        if attempt == len(_RETRY_DELAYS):
            raise _unavailable(reason)
        delay = _retry_delay(attempt, resp)
        logger.warning("Graph %s: %s, retrying in %ss (%d/%d)", url_kind, reason, delay, attempt + 1, len(_RETRY_DELAYS))
        await asyncio.sleep(delay)


async def _graph_get(client: httpx.AsyncClient, url: str, token: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}

    async def request() -> dict:
        resp = await client.get(url, headers=headers)
        if resp.status_code in _TRANSIENT_STATUSES:
            raise _Transient(resp)
        if resp.status_code >= 400:
            raise _refused(resp)
        return resp.json()

    return await _with_retries(request, "message list")


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

        async def request() -> bytes:
            async with client.stream("GET", url, headers={"Authorization": f"Bearer {token}"}) as resp:
                if resp.status_code in _TRANSIENT_STATUSES:
                    await resp.aread()
                    raise _Transient(resp)
                if resp.status_code >= 400:
                    await resp.aread()
                    raise _refused(resp)
                chunks: list[bytes] = []
                size = 0
                async for chunk in resp.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_MESSAGE_BYTES:
                        raise MessageTooLargeError(f"message {message_id} is larger than {MAX_MESSAGE_BYTES // (1024 * 1024)} MB")
                    chunks.append(chunk)
                return b"".join(chunks)

        return await _with_retries(request, "message download")
