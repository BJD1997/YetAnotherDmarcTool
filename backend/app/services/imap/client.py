"""Reading DMARC/TLS-RPT reports from an IMAP mailbox.

Python's own imaplib, run in a worker thread (it blocks). Every connection:
- goes to a public address only, and to exactly the address that was checked
  (the outbound connection guard, as for the MTA-STS fetch), so the IMAP
  settings can't be used to reach the server's own network;
- is encrypted: TLS from the start ("ssl", port 993) or upgraded with
  STARTTLS ("starttls", port 143), with the certificate checked against the
  host name. Plain IMAP isn't offered: it would send the password in the clear.

After a report is stored it's marked as read and moved to the processed
folder, which is created when it doesn't exist yet."""

import asyncio
import imaplib
import re
import socket
import ssl
from dataclasses import dataclass

import certifi

from app.models.enums import ImapSecurity
from app.services.dns_checks.ssrf_guard import BlockedAddressError, resolve_public_address
from app.services.ingestion.sender_auth import top_authserv_id

CONNECT_TIMEOUT_SECONDS = 20
MAX_MESSAGE_BYTES = 25 * 1024 * 1024
MAX_MESSAGES_PER_POLL = 500


class ImapError(Exception):
    """Something the person who set up the mailbox can act on; the message is
    shown in Settings and the sync history as it is."""


@dataclass(frozen=True)
class ImapSettings:
    host: str
    port: int
    security: ImapSecurity
    username: str
    password: str
    folder: str
    processed_folder: str


class _PinnedIMAP4(imaplib.IMAP4):
    """Connects to the checked address, not to a fresh lookup of the name."""

    def __init__(self, host: str, port: int, address: str, timeout: float):
        self._address = address
        super().__init__(host, port, timeout=timeout)

    def _create_socket(self, timeout):
        return socket.create_connection((self._address, self.port), timeout)


class _PinnedIMAP4_SSL(imaplib.IMAP4_SSL):
    def __init__(self, host: str, port: int, address: str, timeout: float, ssl_context: ssl.SSLContext):
        self._address = address
        super().__init__(host, port, ssl_context=ssl_context, timeout=timeout)

    def _create_socket(self, timeout):
        sock = socket.create_connection((self._address, self.port), timeout)
        return self.ssl_context.wrap_socket(sock, server_hostname=self.host)


def _quote(folder: str) -> str:
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _check(response: tuple, what: str) -> list:
    status, data = response
    if status != "OK":
        detail = b" ".join(d for d in data if isinstance(d, bytes)).decode(errors="replace").strip()
        raise ImapError(f"{what} failed{': ' + detail if detail else ''}")
    return data


def tls_context() -> ssl.SSLContext:
    """Certificates checked against certifi's list, like every HTTPS call
    (httpx), instead of the container's system list: one list that follows
    Mozilla's trust decisions and is refreshed with every build. (In October
    2026 certifi dropped the emSign roots after eMudhra's intermediate keys
    were stolen, while Debian's list still trusted them.)"""
    return ssl.create_default_context(cafile=certifi.where())


class ImapMailbox:
    """One open, logged-in connection. Use connect() and close()."""

    def __init__(self, client: imaplib.IMAP4, settings: ImapSettings):
        self._client = client
        self._settings = settings
        self._capabilities = {c.upper() for c in client.capabilities}
        self._processed_ready = False

    @classmethod
    def connect(cls, settings: ImapSettings, address: str) -> "ImapMailbox":
        """Blocking: run in a thread. `address` comes from resolve_address()."""
        context = tls_context()
        try:
            if settings.security == ImapSecurity.ssl:
                client = _PinnedIMAP4_SSL(settings.host, settings.port, address, CONNECT_TIMEOUT_SECONDS, context)
            else:
                client = _PinnedIMAP4(settings.host, settings.port, address, CONNECT_TIMEOUT_SECONDS)
                if "STARTTLS" not in {c.upper() for c in client.capabilities}:
                    client.logout()
                    raise ImapError("the server doesn't offer STARTTLS: choose SSL/TLS (usually port 993)")
                client.starttls(ssl_context=context)
        except ssl.SSLCertVerificationError as exc:
            raise ImapError(f"the server's certificate isn't valid for {settings.host}: {exc.verify_message}") from exc
        except (ssl.SSLError, imaplib.IMAP4.error) as exc:
            raise ImapError(f"couldn't set up an encrypted connection: {exc}") from exc
        except OSError as exc:
            raise ImapError(f"couldn't connect to {settings.host}:{settings.port}: {exc.strerror or exc}") from exc
        try:
            client.login(settings.username, settings.password)
        except imaplib.IMAP4.error as exc:
            client.logout()
            raise ImapError("sign-in refused: check the username and password (often an app password)") from exc
        return cls(client, settings)

    def close(self) -> None:
        try:
            self._client.logout()
        except (imaplib.IMAP4.error, OSError):
            pass

    def select_folder(self, *, readonly: bool = False) -> int:
        """Opens the folder reports are read from; returns its message count."""
        try:
            data = _check(self._client.select(_quote(self._settings.folder), readonly=readonly), "opening the folder")
        except ImapError as exc:
            raise ImapError(f'the folder "{self._settings.folder}" can\'t be opened ({exc})') from exc
        return int(data[0]) if data and data[0] else 0

    def newest_authserv_id(self) -> str | None:
        """The ID in the top Authentication-Results header of the newest
        message in the folder: who the mailbox's own server says it is."""
        data = _check(self._client.uid("SEARCH", None, "ALL"), "listing messages")
        uids = (data[0] or b"").split()
        if not uids:
            return None
        data = _check(
            self._client.uid("FETCH", uids[-1], "(BODY.PEEK[HEADER.FIELDS (AUTHENTICATION-RESULTS)])"),
            "reading a message's headers",
        )
        for part in data:
            if isinstance(part, tuple) and len(part) == 2:
                return top_authserv_id(part[1])
        return None

    def unread_message_uids(self) -> list[bytes]:
        data = _check(self._client.uid("SEARCH", None, "UNSEEN"), "searching for unread messages")
        return (data[0] or b"").split()[:MAX_MESSAGES_PER_POLL]

    def message_size(self, uid: bytes) -> int:
        data = _check(self._client.uid("FETCH", uid, "(RFC822.SIZE)"), "reading a message's size")
        match = re.search(rb"RFC822\.SIZE (\d+)", data[0] if isinstance(data[0], bytes) else data[0][0])
        return int(match.group(1)) if match else 0

    def fetch_message(self, uid: bytes) -> bytes:
        """The raw message, without marking it read (BODY.PEEK)."""
        data = _check(self._client.uid("FETCH", uid, "(BODY.PEEK[])"), "fetching a message")
        for part in data:
            if isinstance(part, tuple) and len(part) == 2:
                return part[1]
        raise ImapError("the server returned an empty message")

    def mark_read(self, uid: bytes) -> None:
        _check(self._client.uid("STORE", uid, "+FLAGS", r"(\Seen)"), "marking a message as read")

    def _ensure_processed_folder(self) -> None:
        if self._processed_ready:
            return
        status, _ = self._client.create(_quote(self._settings.processed_folder))
        # NO usually means it already exists; anything else shows up below
        # when the move itself fails.
        self._processed_ready = True if status in ("OK", "NO") else False
        # Some mail apps and webmail only list subscribed folders; without
        # this the folder exists but can't be seen there.
        try:
            self._client.subscribe(_quote(self._settings.processed_folder))
        except imaplib.IMAP4.error:
            pass

    def mark_read_and_move(self, uid: bytes) -> None:
        self.mark_read(uid)
        self._ensure_processed_folder()
        target = _quote(self._settings.processed_folder)
        if "MOVE" in self._capabilities:
            _check(self._client.uid("MOVE", uid, target), "moving a message")
            return
        _check(self._client.uid("COPY", uid, target), "copying a message")
        _check(self._client.uid("STORE", uid, "+FLAGS", r"(\Deleted)"), "removing the original")
        if "UIDPLUS" in self._capabilities:
            self._client.uid("EXPUNGE", uid)
        else:
            self._client.expunge()


async def resolve_address(settings: ImapSettings, *, allow_private: bool = False) -> str:
    """The address to connect to. Organizations' mailboxes must be on a
    public address; the hosted mailbox (set by the platform admin) may be
    on the server's own network."""
    if allow_private:
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(settings.host, settings.port, proto=socket.IPPROTO_TCP)
        except socket.gaierror as exc:
            raise ImapError(f"could not resolve {settings.host}: {exc}") from exc
        return infos[0][4][0]
    try:
        return await resolve_public_address(settings.host, settings.port)
    except BlockedAddressError as exc:
        raise ImapError(str(exc)) from exc
