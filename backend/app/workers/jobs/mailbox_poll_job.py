"""One run = one organization's mailbox. Progress is committed incrementally
every COMMIT_BATCH_SIZE messages (not just once at the end) — a poll run
against a large backlog can take minutes, and holding thousands of inserts
in one uncommitted transaction means any interruption (container restart,
crash, OOM) loses *all* of it. mailbox_connections.delta_link is only
persisted in the FINAL commit though: advancing it early, before every
message in this run has actually been processed, would permanently skip
whatever hadn't been reached yet. If a run is interrupted after an interim
commit, the next run re-fetches the same message set (delta_link wasn't
advanced) — already-written reports are silently skipped via report_writer's
natural-key idempotency, so this only costs some redundant re-fetch/re-parse
work, never duplicate rows or lost reports.

On success, delta_link/job stats are committed together at the end; on
failure, a fresh session records the failure (rather than trying to reuse
the failed session's transaction, which SQLAlchemy will have expired/rolled
back) so mailbox_connections and job_runs reliably reflect what happened
even when the poll itself blew up partway through.

A Postgres session-level advisory lock, keyed per-org, prevents two runs for
the same org overlapping — this can genuinely happen (a manually-triggered
resync racing the scheduler's own interval trigger, or a worker restart
re-registering a job while a manual run from before the restart is still
in flight, as actually happened once during development: the same 4,500+
message backlog got fetched and parsed twice concurrently, wastefully but
harmlessly since report_writer's idempotency caught every duplicate). The
lock is held on its own dedicated connection, independent of the ORM
session(s) below — session-level advisory locks live on the physical
connection that acquired them, and this function's periodic commits would
otherwise risk SQLAlchemy handing back a *different* pooled connection
between commits, silently losing the lock mid-run."""

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text

from app.db.rls import set_org_context
from app.db.session import async_session_factory, engine
from app.models.enums import ConsentStatus, JobStatus, JobType, SyncStatus
from app.models.job_run import JobRun
from app.models.mailbox_connection import MailboxConnection
from app.services.graph.mailbox_poller import fetch_message_raw_mime, fetch_new_message_ids
from app.services.ingestion import report_writer
from app.services.ingestion.parsedmarc_adapter import UnparseableReportError, parse_report_email

logger = logging.getLogger(__name__)

COMMIT_BATCH_SIZE = 100


def _empty_stats() -> dict:
    return {
        "messages_seen": 0,
        "aggregate_reports": 0,
        "forensic_reports": 0,
        "tls_rpt_policies": 0,
        "errors": 0,
    }


def _advisory_lock_key(organization_id: uuid.UUID) -> int:
    # pg_advisory_lock takes a bigint; derive one deterministically from the
    # org's UUID rather than needing a round-trip to hash it DB-side.
    return int.from_bytes(organization_id.bytes[:8], "big", signed=True)


async def poll_org_mailbox(organization_id: uuid.UUID, tenant_id: str) -> None:
    lock_key = _advisory_lock_key(organization_id)
    async with engine.connect() as lock_conn:
        got_lock = (
            await lock_conn.execute(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key})
        ).scalar_one()
        # Advisory lock acquisition takes effect immediately server-side,
        # independent of transaction commit — but commit anyway so this
        # connection isn't sitting idle-in-transaction for the whole poll.
        await lock_conn.commit()
        if not got_lock:
            logger.info("mailbox poll for org %s already running elsewhere — skipping this trigger", organization_id)
            return
        try:
            await _do_poll(organization_id, tenant_id)
        finally:
            await lock_conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key})
            await lock_conn.commit()


async def _do_poll(organization_id: uuid.UUID, tenant_id: str) -> None:
    started_at = datetime.now(timezone.utc)
    stats = _empty_stats()

    try:
        async with async_session_factory() as db:
            await set_org_context(db, organization_id)

            result = await db.execute(
                select(MailboxConnection).where(MailboxConnection.organization_id == organization_id)
            )
            connection = result.scalar_one_or_none()
            if connection is None or connection.consent_status != ConsentStatus.granted:
                return

            message_ids, new_delta_link = await fetch_new_message_ids(
                tenant_id=tenant_id, mailbox=connection.mailbox_address, delta_link=connection.delta_link
            )
            stats["messages_seen"] = len(message_ids)

            for i, message_id in enumerate(message_ids, start=1):
                try:
                    raw_mime = await fetch_message_raw_mime(
                        tenant_id=tenant_id, mailbox=connection.mailbox_address, message_id=message_id
                    )
                    parsed = parse_report_email(raw_mime)
                except UnparseableReportError:
                    stats["errors"] += 1
                    continue
                except Exception:
                    # A single inaccessible/malformed message shouldn't sink
                    # the rest of the batch — log and move on. If the mailbox
                    # or auth is genuinely broken, every message will fail
                    # this way and the pattern will be obvious in job_runs.
                    logger.exception("failed to fetch/parse message %s (org %s)", message_id, organization_id)
                    stats["errors"] += 1
                    continue

                report_type = parsed["report_type"]
                report = parsed["report"]
                if report_type == "aggregate":
                    if await report_writer.write_aggregate_report(db, organization_id, report, message_id):
                        stats["aggregate_reports"] += 1
                elif report_type == "forensic":
                    if await report_writer.write_forensic_report(db, organization_id, report, message_id):
                        stats["forensic_reports"] += 1
                elif report_type == "smtp_tls":
                    stats["tls_rpt_policies"] += await report_writer.write_smtp_tls_report(
                        db, organization_id, report, message_id
                    )

                if i % COMMIT_BATCH_SIZE == 0:
                    await db.commit()
                    # Commit ends the transaction the RLS context was set
                    # in — re-set it before the next iteration touches any
                    # RLS-protected table again (see app/db/rls.py).
                    await set_org_context(db, organization_id)
                    logger.info(
                        "mailbox poll org %s: committed progress %d/%d messages",
                        organization_id, i, len(message_ids),
                    )

            connection.delta_link = new_delta_link
            connection.last_sync_at = datetime.now(timezone.utc)
            connection.last_sync_status = SyncStatus.success
            connection.last_sync_error = None

            db.add(
                JobRun(
                    job_type=JobType.mailbox_poll,
                    organization_id=organization_id,
                    status=JobStatus.success,
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                    stats=stats,
                )
            )
            await db.commit()

    except Exception as exc:
        logger.exception("mailbox poll failed for org %s", organization_id)
        async with async_session_factory() as db:
            await set_org_context(db, organization_id)
            result = await db.execute(
                select(MailboxConnection).where(MailboxConnection.organization_id == organization_id)
            )
            connection = result.scalar_one_or_none()
            if connection is not None:
                connection.last_sync_status = SyncStatus.error
                connection.last_sync_error = str(exc)[:2000]
            db.add(
                JobRun(
                    job_type=JobType.mailbox_poll,
                    organization_id=organization_id,
                    status=JobStatus.failure,
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc),
                    error_message=str(exc)[:2000],
                    stats=stats,
                )
            )
            await db.commit()
