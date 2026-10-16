"""Polls the operator's own shared reporting mailbox (see Domain.
hosted_report_address / POST /domains/{id}/hosted-report-address) — one
mailbox shared across every customer using a hosted address, unlike
mailbox_poll_job.py's per-org mailboxes. Each message's recipient is
matched against every domain's hosted_report_address to work out which
org it belongs to, then handed to the exact same report_writer path the
per-org poller uses. Read from Microsoft 365 (Graph) or over IMAP, per
HOSTED_REPORTS_SOURCE; no-ops entirely until the hosted mailbox is
configured (settings.hosted_mailbox_ready, see app/config.py)."""

import asyncio
import email.utils
import logging
from datetime import datetime, timezone

from sqlalchemy import text

from app.config import settings
from app.db.rls import set_org_context, set_platform_admin_context
from app.db.session import async_session_factory, engine
from app.models.enums import ImapSecurity, SyncStatus
from app.repositories.domains import list_domains_with_hosted_report_address
from app.repositories.mailbox_connections import get_or_create_hosted_reports_poll_state
from app.services.graph.mailbox_poller import MessageTooLargeError, fetch_message_raw_mime, fetch_new_message_ids
from app.services.gmail.client import AUTHSERV_ID as GMAIL_AUTHSERV_ID
from app.services.gmail.poll import mailbox_for as gmail_mailbox_for
from app.services.gmail.poll import message_id as gmail_message_id
from app.services.imap.client import MAX_MESSAGE_BYTES, ImapError, ImapMailbox, ImapSettings, resolve_address
from app.services.imap.poll import message_id as imap_message_id
from app.services.ingestion import report_writer
from app.services.ingestion.parsedmarc_adapter import UnparseableReportError, parse_report_email
from app.services.ingestion.sender_auth import authenticate_report_sender

logger = logging.getLogger(__name__)

_ADVISORY_LOCK_KEY = 0x484F5354  # "HOST" — fixed, since there's only ever one instance of this job


def _recipient_addresses(raw_mime: bytes) -> list[str]:
    """To first (where reporters put the rua= address); then what the
    receiving server recorded, for providers that rewrite To."""
    msg = email.message_from_bytes(raw_mime)
    headers = [msg.get("To", "") or ""] + (msg.get_all("Delivered-To") or []) + (msg.get_all("X-Original-To") or [])
    return [addr.lower() for _name, addr in email.utils.getaddresses(headers) if addr]


async def poll_hosted_reports_mailbox() -> None:
    if not settings.hosted_mailbox_ready:
        return

    async with engine.connect() as lock_conn:
        got_lock = (await lock_conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY})).scalar_one()
        await lock_conn.commit()
        if not got_lock:
            logger.info("hosted reports poll already running elsewhere — skipping this trigger")
            return
        try:
            readers = {"imap": _do_imap_poll, "google": _do_google_poll}
            await readers.get(settings.hosted_reports_source, _do_poll)()
        finally:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY})
            await lock_conn.commit()


async def _do_poll() -> None:
    started_at = datetime.now(timezone.utc)
    messages_seen = 0
    unmatched = 0
    unverified = 0
    errors = 0

    try:
        async with async_session_factory() as db:
            await set_platform_admin_context(db, is_admin=True)
            state = await get_or_create_hosted_reports_poll_state(db)

            address_map = {
                d.hosted_report_address.lower(): (d.id, d.organization_id)
                for d in await list_domains_with_hosted_report_address(db)
            }

            message_ids, new_delta_link = await fetch_new_message_ids(
                tenant_id=settings.hosted_reports_tenant_id,
                mailbox=settings.hosted_reports_mailbox_address,
                delta_link=state.delta_link,
            )
            messages_seen = len(message_ids)

            for message_id in message_ids:
                try:
                    raw_mime = await fetch_message_raw_mime(
                        tenant_id=settings.hosted_reports_tenant_id,
                        mailbox=settings.hosted_reports_mailbox_address,
                        message_id=message_id,
                    )
                except MessageTooLargeError as exc:
                    logger.warning("skipped hosted-reports message: %s", exc)
                    errors += 1
                    continue
                except Exception:
                    logger.exception("failed to fetch hosted-reports message %s", message_id)
                    errors += 1
                    continue

                matched_org_id = None
                for addr in _recipient_addresses(raw_mime):
                    if addr in address_map:
                        _domain_id, matched_org_id = address_map[addr]
                        break
                if matched_org_id is None:
                    logger.info("hosted-reports message %s recipient matched no known domain — skipping", message_id)
                    unmatched += 1
                    continue

                try:
                    parsed = parse_report_email(raw_mime)
                except UnparseableReportError:
                    errors += 1
                    continue
                except Exception:
                    logger.exception("failed to parse hosted-reports message %s", message_id)
                    errors += 1
                    continue

                # This mailbox is shared by every org with a hosted address,
                # so one bad report failing the run would stall ingestion for
                # all of them — see mailbox_poll_job's matching savepoint.
                await set_org_context(db, matched_org_id)
                try:
                    async with db.begin_nested():
                        written = await report_writer.write_report(
                            db, matched_org_id, parsed, message_id, authenticate_report_sender(raw_mime)
                        )
                    unverified += written.get("unverified_senders", 0)
                except Exception:
                    logger.exception("failed to store hosted-reports message %s (org %s)", message_id, matched_org_id)
                    errors += 1
                # Back to the platform-admin bypass for the next message's
                # address_map-independent work (already in memory) and the
                # final state update below, which isn't org-scoped data.
                await set_platform_admin_context(db, is_admin=True)

            state.delta_link = new_delta_link
            state.last_sync_at = datetime.now(timezone.utc)
            state.last_sync_status = SyncStatus.success
            state.last_sync_error = None
            await db.commit()
            logger.info(
                "hosted reports poll: %d message(s) seen, %d unmatched, %d unverified sender(s), %d error(s)",
                messages_seen, unmatched, unverified, errors,
            )

    except Exception as exc:
        logger.exception("hosted reports poll failed")
        async with async_session_factory() as db:
            await set_platform_admin_context(db, is_admin=True)
            state = await get_or_create_hosted_reports_poll_state(db)
            state.last_sync_at = datetime.now(timezone.utc)
            state.last_sync_status = SyncStatus.error
            state.last_sync_error = str(exc)[:2000]
            await db.commit()



def _imap_settings() -> ImapSettings:
    return ImapSettings(
        host=settings.hosted_reports_imap_host or "",
        port=settings.hosted_reports_imap_port,
        security=ImapSecurity(settings.hosted_reports_imap_security),
        username=settings.hosted_reports_imap_username or "",
        password=settings.hosted_reports_imap_password or "",
        folder=settings.hosted_reports_imap_folder,
        processed_folder=settings.hosted_reports_imap_processed_folder,
    )


class _ImapReader:
    """The hosted mailbox over IMAP, as the async calls _read_hosted uses."""

    def __init__(self, mailbox: ImapMailbox):
        self._mailbox = mailbox

    async def unread_ids(self):
        await asyncio.to_thread(self._mailbox.select_folder)
        return await asyncio.to_thread(self._mailbox.unread_message_uids)

    async def server_id(self):
        return await asyncio.to_thread(self._mailbox.newest_authserv_id)

    async def size(self, uid):
        return await asyncio.to_thread(self._mailbox.message_size, uid)

    async def fetch(self, uid):
        return await asyncio.to_thread(self._mailbox.fetch_message, uid)

    async def mark_read(self, uid):
        await asyncio.to_thread(self._mailbox.mark_read, uid)

    async def file_away(self, uid):
        await asyncio.to_thread(self._mailbox.mark_read_and_move, uid)

    def message_id(self, raw, uid):
        return imap_message_id(raw)


class _GmailReader:
    """The hosted mailbox in Google Workspace (the server's service account)."""

    def __init__(self, mailbox):
        self._mailbox = mailbox

    async def unread_ids(self):
        return await self._mailbox.unread_ids()

    async def server_id(self):
        return GMAIL_AUTHSERV_ID

    async def size(self, gmail_id):
        return await self._mailbox.size(gmail_id)

    async def fetch(self, gmail_id):
        return await self._mailbox.fetch_raw(gmail_id)

    async def mark_read(self, gmail_id):
        await self._mailbox.mark_read(gmail_id)

    async def file_away(self, gmail_id):
        await self._mailbox.mark_read_and_move(gmail_id)

    def message_id(self, raw, gmail_id):
        return gmail_message_id(raw, gmail_id)


def _imap_settings() -> ImapSettings:
    return ImapSettings(
        host=settings.hosted_reports_imap_host or "",
        port=settings.hosted_reports_imap_port,
        security=ImapSecurity(settings.hosted_reports_imap_security),
        username=settings.hosted_reports_imap_username or "",
        password=settings.hosted_reports_imap_password or "",
        folder=settings.hosted_reports_imap_folder,
        processed_folder=settings.hosted_reports_imap_processed_folder,
    )


async def _do_imap_poll() -> None:
    async def _open():
        imap = _imap_settings()
        address = await resolve_address(imap, allow_private=True)
        mailbox = await asyncio.to_thread(ImapMailbox.connect, imap, address)
        return _ImapReader(mailbox), lambda: asyncio.to_thread(mailbox.close)

    await _poll_with(_open, (settings.hosted_reports_imap_authserv_id or "").strip().lower(), "IMAP")


async def _do_google_poll() -> None:
    async def _open():
        mailbox = gmail_mailbox_for(settings.hosted_reports_mailbox_address or "")

        async def _close():
            return None

        return _GmailReader(mailbox), _close

    await _poll_with(_open, GMAIL_AUTHSERV_ID, "Google")


async def _poll_with(open_reader, trusted_id: str, source: str) -> None:
    """The hosted mailbox over IMAP or from Google: unread messages, routed
    by recipient. A stored report is filed away (after it's committed, so an
    interruption can't lose it); mail that isn't a report, or isn't for a
    known address, is only marked read. Nothing is read until the mail
    server's ID is known (always, for Google)."""
    started_at = datetime.now(timezone.utc)
    stats = {"messages_seen": 0, "unmatched": 0, "unverified": 0, "errors": 0}
    try:
        reader, close = await open_reader()
        try:
            ids = await reader.unread_ids()
            if not trusted_id:
                if ids:
                    detected = await reader.server_id()
                    raise ImapError(
                        f"set HOSTED_REPORTS_IMAP_AUTHSERV_ID to your mail server's ID ({detected or 'none found'}) "
                        "before reports are read"
                    )
                return
            stats["messages_seen"] = len(ids)
            async with async_session_factory() as db:
                await set_platform_admin_context(db, is_admin=True)
                address_map = {
                    d.hosted_report_address.lower(): (d.id, d.organization_id)
                    for d in await list_domains_with_hosted_report_address(db)
                }
                for message in ids:
                    if await reader.size(message) > MAX_MESSAGE_BYTES:
                        logger.warning("skipped hosted message %s: larger than 25 MB", message)
                        await reader.mark_read(message)
                        stats["errors"] += 1
                        continue
                    raw = await reader.fetch(message)
                    outcome = await _store(
                        db, address_map, raw, reader.message_id(raw, message),
                        authenticate_report_sender(raw, imap_authserv_id=trusted_id), stats,
                    )
                    if outcome == "stored":
                        await db.commit()
                        await set_platform_admin_context(db, is_admin=True)
                        await reader.file_away(message)
                    else:
                        await reader.mark_read(message)
                state = await get_or_create_hosted_reports_poll_state(db)
                state.last_sync_at = datetime.now(timezone.utc)
                state.last_sync_status = SyncStatus.success
                state.last_sync_error = None
                await db.commit()
        finally:
            await close()
        logger.info(
            "hosted reports poll (%s): %d message(s) seen, %d unmatched, %d unverified sender(s), %d error(s) since %s",
            source, stats["messages_seen"], stats["unmatched"], stats["unverified"], stats["errors"], started_at.isoformat(),
        )
    except Exception as exc:
        logger.exception("hosted reports poll (%s) failed", source)
        async with async_session_factory() as db:
            await set_platform_admin_context(db, is_admin=True)
            state = await get_or_create_hosted_reports_poll_state(db)
            state.last_sync_at = datetime.now(timezone.utc)
            state.last_sync_status = SyncStatus.error
            state.last_sync_error = str(exc)[:2000]
            await db.commit()


async def _store(db, address_map: dict, raw: bytes, message_id: str, sender, stats: dict) -> str:
    """Routes one message by recipient and stores its report in that
    organization: "stored", "unmatched", "unparseable" or "error". Used by
    the IMAP and Google readers; one bad report never stops the others."""
    matched_org_id = None
    for addr in _recipient_addresses(raw):
        if addr in address_map:
            _domain_id, matched_org_id = address_map[addr]
            break
    if matched_org_id is None:
        stats["unmatched"] += 1
        return "unmatched"
    try:
        parsed = parse_report_email(raw)
    except UnparseableReportError:
        stats["errors"] += 1
        return "unparseable"
    await set_org_context(db, matched_org_id)
    try:
        async with db.begin_nested():
            written = await report_writer.write_report(db, matched_org_id, parsed, message_id, sender)
        stats["unverified"] += written.get("unverified_senders", 0)
        return "stored"
    except Exception:
        logger.exception("failed to store hosted report %s (org %s)", message_id, matched_org_id)
        stats["errors"] += 1
        return "error"
    finally:
        await set_platform_admin_context(db, is_admin=True)
