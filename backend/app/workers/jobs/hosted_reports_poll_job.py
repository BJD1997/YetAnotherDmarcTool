"""Polls the operator's own shared reporting mailbox (see Domain.
hosted_report_address / POST /domains/{id}/hosted-report-address) — one
mailbox shared across every customer using a hosted address, unlike
mailbox_poll_job.py's per-org mailboxes. Each message's recipient is
matched against every domain's hosted_report_address to work out which
org it belongs to, then handed to the exact same report_writer path the
per-org poller uses. No-ops entirely until HOSTED_REPORTS_TENANT_ID/
HOSTED_REPORTS_MAILBOX_ADDRESS are configured (see app/config.py)."""

import email.utils
import logging
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.rls import set_org_context, set_platform_admin_context
from app.db.session import async_session_factory, engine
from app.models.domain import Domain
from app.models.enums import SyncStatus
from app.models.hosted_reports_poll_state import HostedReportsPollState
from app.services.graph.mailbox_poller import fetch_message_raw_mime, fetch_new_message_ids
from app.services.ingestion import report_writer
from app.services.ingestion.parsedmarc_adapter import UnparseableReportError, parse_report_email

logger = logging.getLogger(__name__)

_ADVISORY_LOCK_KEY = 0x484F5354  # "HOST" — fixed, since there's only ever one instance of this job


def _recipient_addresses(raw_mime: bytes) -> list[str]:
    msg = email.message_from_bytes(raw_mime)
    to_header = msg.get("To", "") or ""
    return [addr.lower() for _name, addr in email.utils.getaddresses([to_header]) if addr]


async def _get_or_create_state(db: AsyncSession) -> HostedReportsPollState:
    result = await db.execute(select(HostedReportsPollState).limit(1))
    state = result.scalar_one_or_none()
    if state is None:
        state = HostedReportsPollState()
        db.add(state)
        await db.flush()
    return state


async def poll_hosted_reports_mailbox() -> None:
    if not settings.hosted_reports_tenant_id or not settings.hosted_reports_mailbox_address:
        return

    async with engine.connect() as lock_conn:
        got_lock = (await lock_conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY})).scalar_one()
        await lock_conn.commit()
        if not got_lock:
            logger.info("hosted reports poll already running elsewhere — skipping this trigger")
            return
        try:
            await _do_poll()
        finally:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY})
            await lock_conn.commit()


async def _do_poll() -> None:
    started_at = datetime.now(timezone.utc)
    messages_seen = 0
    unmatched = 0
    errors = 0

    try:
        async with async_session_factory() as db:
            await set_platform_admin_context(db, is_admin=True)
            state = await _get_or_create_state(db)

            address_map = {
                d.hosted_report_address.lower(): (d.id, d.organization_id)
                for d in (
                    await db.execute(select(Domain).where(Domain.hosted_report_address.is_not(None)))
                ).scalars()
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

                await set_org_context(db, matched_org_id)
                if parsed["report_type"] == "aggregate":
                    await report_writer.write_aggregate_report(db, matched_org_id, parsed["report"], message_id)
                elif parsed["report_type"] == "forensic":
                    await report_writer.write_forensic_report(db, matched_org_id, parsed["report"], message_id)
                elif parsed["report_type"] == "smtp_tls":
                    await report_writer.write_smtp_tls_report(db, matched_org_id, parsed["report"], message_id)
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
                "hosted reports poll: %d message(s) seen, %d unmatched, %d error(s)", messages_seen, unmatched, errors
            )

    except Exception as exc:
        logger.exception("hosted reports poll failed")
        async with async_session_factory() as db:
            await set_platform_admin_context(db, is_admin=True)
            state = await _get_or_create_state(db)
            state.last_sync_at = datetime.now(timezone.utc)
            state.last_sync_status = SyncStatus.error
            state.last_sync_error = str(exc)[:2000]
            await db.commit()
