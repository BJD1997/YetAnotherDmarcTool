# backend/app/workers/scheduler.py
"""Entrypoint for the `worker` container (`python -m app.workers.scheduler`).

Every replica runs a pool of queue-consumer loops that drain `background_jobs`
(claimed with `FOR UPDATE SKIP LOCKED`, so N replicas share the work without
double-processing). Exactly one replica — whichever holds the Postgres advisory
lock — additionally acts as the **leader**: on a cadence it enqueues the
recurring work (per-org mailbox polls, the DNS/verification sweeps, retention
purge, update check, rate-limit-hit pruning) and reclaims jobs stranded by a
crashed worker. See app/services/jobs/queue.py and app/services/jobs/leader.py.

This replaced the previous single in-process AsyncIOScheduler so the worker can
scale to multiple replicas — `docker compose up --scale worker=N`, or a KEDA
Postgres-queue-depth scaler on Azure Container Apps. Everything the leader
enqueues is dedupe-keyed and every handler is idempotent, so at-least-once
delivery (a job re-run after its worker died) is safe.
"""

import asyncio
import logging
import os
import socket
import time
import uuid

from app.config import settings
from app.db.rls import set_platform_admin_context
from app.db.session import async_session_factory
from app.repositories.mailbox_connections import list_orgs_with_granted_mailbox_connections
from app.services.auth.rate_limit import prune_rate_limit_hits
from app.services.dns_checks.domain_verification import run_domain_verification_sweep
from app.services.dns_checks.scheduled_recheck import DNS_CHECK_SWEEP_TICK_SECONDS, run_dns_check_sweep
from app.services.jobs import queue
from app.services.jobs.leader import LeaderLock
from app.services.jobs.queue import prune_finished_jobs
from app.services.retention.forensic_purge import run_retention_purge
from app.services.update_check import run_update_check
from app.workers.health import heartbeat, start_health_server
from app.workers.jobs.hosted_reports_poll_job import poll_hosted_reports_mailbox
from app.workers.jobs.mailbox_poll_job import poll_org_mailbox

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("worker")

MAILBOX_POLL_INTERVAL_SECONDS = 600
DOMAIN_VERIFICATION_SWEEP_INTERVAL_SECONDS = 300
HOSTED_REPORTS_POLL_INTERVAL_SECONDS = 600
RETENTION_PURGE_INTERVAL_SECONDS = 24 * 3600
UPDATE_CHECK_INTERVAL_SECONDS = 6 * 3600
RATE_LIMIT_PRUNE_INTERVAL_SECONDS = 3600
BACKGROUND_JOBS_PRUNE_INTERVAL_SECONDS = 24 * 3600
REAP_INTERVAL_SECONDS = 60
LEADER_TICK_SECONDS = 15
HEARTBEAT_INTERVAL_SECONDS = 5

# Recurring cluster-wide sweeps -> interval. dedupe_key == job_type keeps at most
# one pending/running instance of each, however far behind the workers get.
_SINGLETON_INTERVALS = {
    "dns_check_sweep": DNS_CHECK_SWEEP_TICK_SECONDS,
    "domain_verification_sweep": DOMAIN_VERIFICATION_SWEEP_INTERVAL_SECONDS,
    "hosted_reports_poll": HOSTED_REPORTS_POLL_INTERVAL_SECONDS,
    "retention_purge": RETENTION_PURGE_INTERVAL_SECONDS,
    "update_check": UPDATE_CHECK_INTERVAL_SECONDS,
    "rate_limit_prune": RATE_LIMIT_PRUNE_INTERVAL_SECONDS,
    "background_jobs_prune": BACKGROUND_JOBS_PRUNE_INTERVAL_SECONDS,
}


# --- job handlers: reuse the existing functions unchanged ---

def _ignoring_payload(coro_fn):
    async def _handler(_payload: dict) -> None:
        await coro_fn()

    return _handler


async def _handle_mailbox_poll(payload: dict) -> None:
    await poll_org_mailbox(uuid.UUID(payload["org_id"]), payload["tenant_id"])


def _register_handlers() -> None:
    queue.register_handler("mailbox_poll", _handle_mailbox_poll)
    queue.register_handler("dns_check_sweep", _ignoring_payload(run_dns_check_sweep))
    queue.register_handler("domain_verification_sweep", _ignoring_payload(run_domain_verification_sweep))
    queue.register_handler("hosted_reports_poll", _ignoring_payload(poll_hosted_reports_mailbox))
    queue.register_handler("retention_purge", _ignoring_payload(run_retention_purge))
    queue.register_handler("update_check", _ignoring_payload(run_update_check))
    queue.register_handler("rate_limit_prune", _ignoring_payload(prune_rate_limit_hits))
    queue.register_handler("background_jobs_prune", _ignoring_payload(prune_finished_jobs))


# --- leader: enqueue recurring work on a cadence ---

async def _enqueue_mailbox_polls(db) -> None:
    await set_platform_admin_context(db, is_admin=True)
    for org_id, tenant_id in await list_orgs_with_granted_mailbox_connections(db):
        await queue.enqueue(
            db,
            "mailbox_poll",
            {"org_id": str(org_id), "tenant_id": str(tenant_id)},
            dedupe_key=f"mailbox_poll:{org_id}",
        )


async def _leadership_loop(leader: LeaderLock) -> None:
    # monotonic timestamp of the last enqueue per key; missing ⇒ due now, so the
    # first tick after (re)election fires everything (idempotent + deduped).
    last: dict[str, float] = {}
    last_reap = 0.0
    while True:
        try:
            if await leader.try_acquire():
                now = time.monotonic()
                async with async_session_factory() as db:
                    for job_type, interval in _SINGLETON_INTERVALS.items():
                        if now - last.get(job_type, -1e18) >= interval:
                            await queue.enqueue(db, job_type, dedupe_key=job_type)
                            last[job_type] = now
                    if now - last.get("mailbox", -1e18) >= MAILBOX_POLL_INTERVAL_SECONDS:
                        await _enqueue_mailbox_polls(db)
                        last["mailbox"] = now
                    if now - last_reap >= REAP_INTERVAL_SECONDS:
                        reaped = await queue.reclaim_stalled(db, settings.worker_job_stale_seconds)
                        last_reap = now
                        if reaped:
                            logger.warning("reclaimed %d stalled job(s)", reaped)
        except Exception:
            logger.exception("leadership loop error")
        await asyncio.sleep(LEADER_TICK_SECONDS)


# --- every replica: consume the queue ---

async def _consumer_loop(worker_id: str) -> None:
    while True:
        try:
            did_work = await queue.process_next(worker_id)
        except Exception:
            logger.exception("consumer loop error")
            did_work = False
        if not did_work:
            await asyncio.sleep(settings.worker_queue_poll_interval_seconds)


async def _heartbeat_loop(leader: LeaderLock) -> None:
    """Beats independently of job processing so health reflects event-loop
    liveness (a long async job keeps beating; a wedged loop stops)."""
    while True:
        heartbeat(is_leader=leader.is_leader)
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)


async def main() -> None:
    worker_id = f"{socket.gethostname()}:{os.getpid()}"[:64]
    _register_handlers()
    start_health_server(settings.worker_health_port)
    leader = LeaderLock(settings.leader_lock_key)
    heartbeat(is_leader=False)
    logger.info(
        "worker %s starting (%d consumers, queue poll %ss, leader tick %ss)",
        worker_id,
        settings.worker_concurrency,
        settings.worker_queue_poll_interval_seconds,
        LEADER_TICK_SECONDS,
    )
    tasks = [
        asyncio.create_task(_heartbeat_loop(leader)),
        asyncio.create_task(_leadership_loop(leader)),
    ]
    for _ in range(settings.worker_concurrency):
        tasks.append(asyncio.create_task(_consumer_loop(worker_id)))
    try:
        await asyncio.gather(*tasks)
    finally:
        await leader.release()


if __name__ == "__main__":
    asyncio.run(main())
