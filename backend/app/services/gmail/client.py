"""Reading a Google Workspace mailbox through the server's service account
(domain-wide delegation, scope gmail.modify), with the Gmail REST API. No
Google libraries: the token request is a JWT signed with PyJWT."""

import base64
import dataclasses
import json
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

import httpx
import jwt

from app.config import settings

SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_API = "https://gmail.googleapis.com/gmail/v1/users"
# Gmail stamps incoming mail with this Authentication-Results ID; only its
# checks count for the report sender check.
AUTHSERV_ID = "mx.google.com"
UNREAD_QUERY = "is:unread in:inbox"
MAX_MESSAGES_PER_POLL = 500

client_factory: Callable[[], httpx.AsyncClient] = lambda: httpx.AsyncClient(timeout=20)
_tokens: dict[str, tuple[float, str]] = {}


class GmailError(Exception):
    """Something that stops reading the mailbox; safe to show to an admin."""


@dataclasses.dataclass(frozen=True)
class ServiceAccount:
    client_email: str
    client_id: str
    private_key: str
    token_uri: str


def load_service_account() -> ServiceAccount | None:
    """The configured service account, or None when there isn't one."""
    raw = None
    if settings.google_mail_service_account_file:
        try:
            raw = Path(settings.google_mail_service_account_file).read_text()
        except OSError as exc:
            raise GmailError(f"the Google service account key can't be read: {exc.strerror or exc}") from exc
    elif settings.google_mail_service_account_json:
        raw = settings.google_mail_service_account_json
    if not raw:
        return None
    try:
        data = json.loads(raw)
        if data.get("type") != "service_account":
            raise ValueError("not a service account key")
        return ServiceAccount(
            client_email=data["client_email"], client_id=str(data["client_id"]),
            private_key=data["private_key"], token_uri=data.get("token_uri") or "https://oauth2.googleapis.com/token",
        )
    except (ValueError, KeyError, TypeError) as exc:
        raise GmailError(f"the Google service account key isn't a valid JSON key: {exc}") from exc


def clear_tokens() -> None:
    _tokens.clear()


async def access_token(account: ServiceAccount, mailbox: str) -> str:
    """A token to act as `mailbox` (cached until shortly before it expires)."""
    cached = _tokens.get(mailbox)
    if cached and cached[0] > time.time() + 60:
        return cached[1]
    now = int(time.time())
    assertion = jwt.encode(
        {"iss": account.client_email, "sub": mailbox, "scope": SCOPE, "aud": account.token_uri, "iat": now, "exp": now + 3600},
        account.private_key,
        algorithm="RS256",
    )
    try:
        async with client_factory() as client:
            response = await client.post(
                account.token_uri,
                data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
            )
    except httpx.HTTPError as exc:
        raise GmailError(f"couldn't reach Google: {exc.__class__.__name__}") from exc
    body = _json(response)
    if response.status_code != 200 or "access_token" not in body:
        error = body.get("error")
        if error == "unauthorized_client":
            raise GmailError(
                f"Google refused: the Workspace admin hasn't granted domain-wide delegation to client ID "
                f"{account.client_id} with scope {SCOPE}"
            )
        if error == "invalid_grant":
            raise GmailError(f"Google refused {mailbox}: check that the mailbox exists in a Workspace that granted access")
        raise GmailError(f"Google refused the sign-in ({error or response.status_code})")
    _tokens[mailbox] = (now + int(body.get("expires_in", 3600)), body["access_token"])
    return body["access_token"]


def _json(response: httpx.Response) -> dict:
    try:
        data = response.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


class GmailMailbox:
    """One mailbox, read as itself through the service account."""

    def __init__(self, account: ServiceAccount, address: str, processed_label: str = "DMARC processed"):
        self._account = account
        self._address = address
        self._processed_label = processed_label
        self._label_id: str | None = None

    async def _call(self, method: str, path: str, **kwargs) -> dict:
        token = await access_token(self._account, self._address)
        url = f"{GMAIL_API}/{quote(self._address, safe='@')}/{path}"
        try:
            async with client_factory() as client:
                response = await client.request(method, url, headers={"Authorization": f"Bearer {token}"}, **kwargs)
        except httpx.HTTPError as exc:
            raise GmailError(f"couldn't reach Gmail: {exc.__class__.__name__}") from exc
        if response.status_code >= 400:
            message = (_json(response).get("error") or {}).get("message") if isinstance(_json(response).get("error"), dict) else None
            raise GmailError(f"Gmail answered {response.status_code}: {message or 'request refused'}")
        return _json(response)

    async def inbox_count(self) -> int:
        return int((await self._call("GET", "labels/INBOX")).get("messagesTotal", 0))

    async def unread_ids(self) -> list[str]:
        data = await self._call("GET", "messages", params={"q": UNREAD_QUERY, "maxResults": MAX_MESSAGES_PER_POLL})
        return [m["id"] for m in data.get("messages", [])]

    async def size(self, message_id: str) -> int:
        return int((await self._call("GET", f"messages/{message_id}", params={"format": "minimal"})).get("sizeEstimate", 0))

    async def fetch_raw(self, message_id: str) -> bytes:
        raw = (await self._call("GET", f"messages/{message_id}", params={"format": "raw"})).get("raw")
        if not raw:
            raise GmailError("Gmail returned an empty message")
        return base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))

    async def mark_read(self, message_id: str) -> None:
        await self._call("POST", f"messages/{message_id}/modify", json={"removeLabelIds": ["UNREAD"]})

    async def mark_read_and_move(self, message_id: str) -> None:
        """Gmail's version of moving to a folder: out of the inbox, under the
        processed label (created when missing)."""
        label_id = await self._processed_label_id()
        await self._call(
            "POST", f"messages/{message_id}/modify",
            json={"addLabelIds": [label_id], "removeLabelIds": ["UNREAD", "INBOX"]},
        )

    async def _processed_label_id(self) -> str:
        if self._label_id:
            return self._label_id
        labels = (await self._call("GET", "labels")).get("labels", [])
        for label in labels:
            if label.get("name") == self._processed_label:
                self._label_id = label["id"]
                return self._label_id
        created = await self._call(
            "POST", "labels",
            json={"name": self._processed_label, "labelListVisibility": "labelShow", "messageListVisibility": "show"},
        )
        self._label_id = created["id"]
        return self._label_id
