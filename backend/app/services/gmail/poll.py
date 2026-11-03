"""Polling an organization's Google Workspace mailbox, and testing it.
Same rules as an IMAP mailbox: a stored report is marked read and filed
under the processed label (after it's committed); a message that isn't a
readable report is only marked read."""

import email
import hashlib
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.rls import set_org_context
from app.models.mailbox_connection import MailboxConnection
from app.services.gmail.client import AUTHSERV_ID, GmailError, GmailMailbox, load_service_account
from app.services.imap.client import MAX_MESSAGE_BYTES
from app.services.ingestion import report_writer
from app.services.ingestion.parsedmarc_adapter import UnparseableReportError, parse_report_email
from app.services.ingestion.sender_auth import authenticate_report_sender

logger = logging.getLogger(__name__)


def mailbox_for(address: str) -> GmailMailbox:
    account = load_service_account()
    if account is None:
        raise GmailError("this server isn't set up for Google Workspace mailboxes")
    return GmailMailbox(account, address)


async def test_mailbox(address: str) -> int:
    """Signs in as the mailbox and reads its inbox count; raises GmailError."""
    return await mailbox_for(address).inbox_count()


def message_id(raw: bytes, gmail_id: str) -> str:
    header = email.message_from_bytes(raw).get("Message-ID", "").strip()
    return header[:255] if header else "gmail:" + (gmail_id or hashlib.sha256(raw).hexdigest())


async def poll_gmail_mailbox(db: AsyncSession, organization_id: uuid.UUID, connection: MailboxConnection, stats: dict) -> None:
    mailbox = mailbox_for(connection.mailbox_address)
    ids = await mailbox.unread_ids()
    stats["messages_seen"] = len(ids)
    for gmail_id in ids:
        if await mailbox.size(gmail_id) > MAX_MESSAGE_BYTES:
            logger.warning("skipped Gmail message %s (org %s): larger than 25 MB", gmail_id, organization_id)
            await mailbox.mark_read(gmail_id)
            stats["errors"] += 1
            continue
        raw = await mailbox.fetch_raw(gmail_id)
        try:
            parsed = parse_report_email(raw)
        except UnparseableReportError:
            await mailbox.mark_read(gmail_id)
            stats["errors"] += 1
            continue
        try:
            async with db.begin_nested():
                written = await report_writer.write_report(
                    db, organization_id, parsed, message_id(raw, gmail_id),
                    authenticate_report_sender(raw, imap_authserv_id=AUTHSERV_ID),
                )
        except Exception:
            logger.exception("failed to store report from Gmail message %s (org %s)", gmail_id, organization_id)
            await mailbox.mark_read(gmail_id)
            stats["errors"] += 1
            continue
        for key, value in written.items():
            stats[key] += value
        await db.commit()
        await set_org_context(db, organization_id)
        await mailbox.mark_read_and_move(gmail_id)
