"""Polling an organization's IMAP mailbox, and testing its settings. The
blocking imaplib calls run in a thread, one at a time on one connection."""

import asyncio
import email
import hashlib
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_org_context
from app.models.enums import ImapSecurity
from app.models.mailbox_connection import MailboxConnection
from app.services.imap.client import MAX_MESSAGE_BYTES, ImapError, ImapMailbox, ImapSettings, resolve_address
from app.services.ingestion import report_writer
from app.services.ingestion.parsedmarc_adapter import UnparseableReportError, parse_report_email
from app.services.ingestion.sender_auth import authenticate_report_sender

logger = logging.getLogger(__name__)

DEFAULT_FOLDER = "INBOX"
DEFAULT_PROCESSED_FOLDER = "DMARC processed"


def settings_from(connection: MailboxConnection) -> ImapSettings:
    if not (connection.imap_host and connection.imap_port and connection.imap_username and connection.imap_password):
        raise ImapError("the IMAP settings are incomplete: save them again in Settings")
    return ImapSettings(
        host=connection.imap_host,
        port=connection.imap_port,
        security=connection.imap_security or ImapSecurity.ssl,
        username=connection.imap_username,
        password=connection.imap_password,
        folder=connection.imap_folder or DEFAULT_FOLDER,
        processed_folder=connection.imap_processed_folder or DEFAULT_PROCESSED_FOLDER,
    )


async def test_connection(settings: ImapSettings) -> tuple[int, str | None]:
    """Connects, signs in and opens the folder (read-only, nothing changes).
    Returns the folder's message count and the mail server's
    Authentication-Results ID seen on its newest message (None when the
    folder is empty); raises ImapError with what's wrong."""
    address = await resolve_address(settings)

    def _run() -> tuple[int, str | None]:
        mailbox = ImapMailbox.connect(settings, address)
        try:
            count = mailbox.select_folder(readonly=True)
            return count, mailbox.newest_authserv_id() if count else None
        finally:
            mailbox.close()

    return await asyncio.to_thread(_run)


def _message_id(raw: bytes) -> str:
    """Stable across polls and folder moves: the email's own Message-ID, so a
    re-delivered or re-read message is recognized as the same report."""
    header = email.message_from_bytes(raw).get("Message-ID", "").strip()
    return header[:255] if header else "imap:" + hashlib.sha256(raw).hexdigest()


async def poll_imap_mailbox(db: AsyncSession, organization_id: uuid.UUID, connection: MailboxConnection, stats: dict) -> None:
    """Reads the unread messages in the report folder. A stored report (or a
    copy of one already stored) is marked read and moved to the processed
    folder; a message that isn't a readable report is only marked read, so it
    stays visible in the folder for a person to look at. Each report is
    committed before its message is moved, so an interruption can't lose it."""
    settings = settings_from(connection)
    trusted_id = (connection.imap_authserv_id or "").lower()
    address = await resolve_address(settings)
    mailbox = await asyncio.to_thread(ImapMailbox.connect, settings, address)
    try:
        await asyncio.to_thread(mailbox.select_folder)
        uids = await asyncio.to_thread(mailbox.unread_message_uids)
        if not trusted_id:
            # Without knowing which Authentication-Results to believe, the
            # sender check can't be done: leave everything unread, note the
            # server's ID for Settings to offer, and say so.
            if uids:
                detected = await asyncio.to_thread(mailbox.newest_authserv_id)
                connection.imap_detected_authserv_id = detected
                await db.commit()
                await set_org_context(db, organization_id)
                raise ImapError(
                    f"confirm your mail server's ID ({detected or 'none found'}) in Settings before reports are read"
                )
            return
        stats["messages_seen"] = len(uids)
        for uid in uids:
            if await asyncio.to_thread(mailbox.message_size, uid) > MAX_MESSAGE_BYTES:
                logger.warning("skipped IMAP message %s (org %s): larger than 25 MB", uid, organization_id)
                await asyncio.to_thread(mailbox.mark_read, uid)
                stats["errors"] += 1
                continue
            raw = await asyncio.to_thread(mailbox.fetch_message, uid)
            try:
                parsed = parse_report_email(raw)
            except UnparseableReportError:
                await asyncio.to_thread(mailbox.mark_read, uid)
                stats["errors"] += 1
                continue
            try:
                async with db.begin_nested():
                    written = await report_writer.write_report(
                        db, organization_id, parsed, _message_id(raw),
                        authenticate_report_sender(raw, imap_authserv_id=trusted_id),
                    )
            except Exception:
                logger.exception("failed to store report from IMAP message %s (org %s)", uid, organization_id)
                await asyncio.to_thread(mailbox.mark_read, uid)
                stats["errors"] += 1
                continue
            for key, value in written.items():
                stats[key] += value
            await db.commit()
            await set_org_context(db, organization_id)
            await asyncio.to_thread(mailbox.mark_read_and_move, uid)
    finally:
        await asyncio.to_thread(mailbox.close)
