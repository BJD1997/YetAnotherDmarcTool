# Horizontal Scale-Out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `worker` and `api` containers safe to run as N replicas — a Postgres-backed work queue with advisory-lock leader election replacing the single-instance `AsyncIOScheduler`, plus a pluggable shared rate limiter, optional PgBouncer compatibility, and optional read-replica routing for report/analytics queries.

**Architecture:** Every worker replica runs consumer loops that claim rows from a new `background_jobs` table via `SELECT ... FOR UPDATE SKIP LOCKED`, so N replicas drain the queue without double-processing. Exactly one replica self-elects leader via a Postgres advisory lock and enqueues due recurring work plus reaps stalled jobs; if it dies, another replica takes over automatically. No message broker — this reuses the Postgres the app already depends on.

**Tech Stack:** FastAPI + SQLAlchemy 2.0 async + Postgres (existing stack). No new dependencies; `apscheduler` is dropped.

**Spec:** `docs/superpowers/specs/2026-09-11-horizontal-scale-out-design.md`

**Reference implementation:** This plan **ports**, rather than redesigns, work that already existed and was live-verified on the (now-superseded) `v0.1.4-beta` branch, commit `fe424bd`. That implementation is the ground truth for every SQL statement, index, and concurrency behavior below — the only structural change from it is splitting its inline queries out into `app/repositories/`, per this codebase's established convention (every task below states exactly what's ported verbatim vs. adapted).

## Global Constraints

- Every repository function takes `db: AsyncSession` as its first argument, returns data, holds no module-level mutable state — matching the existing convention across `app/repositories/`.
- **Repository functions in this plan do not call `db.commit()`** — matching every existing repository function in this codebase. The calling service-layer code (`app/services/jobs/queue.py`, `app/services/auth/rate_limit.py`, `app/workers/scheduler.py`) opens each short-lived session, calls the repository function(s), and commits — mirroring the reference implementation's "each DB step is its own short transaction so a long-running handler never holds one open" design.
- Every existing job handler function (`run_domain_verification_sweep`, `run_dns_check_sweep`, `run_retention_purge`, `poll_hosted_reports_mailbox`, `poll_org_mailbox`, `run_update_check`) is reused **completely unchanged** as a queue handler — no changes to their internals in this plan.
- At-least-once delivery is safe only because every handler above is already idempotent (the repository-extraction refactor's `insert_*_if_new` / upsert patterns). Do not add a new handler in this plan whose effect isn't idempotent.
- `background_jobs` and `rate_limit_hits` are deliberately **not** RLS-scoped and have no `organization_id` column — worker/infra tables, same category as `UpdateCheckState`. Do not add RLS policies to them; do not add them to the RLS meta-guard's expected-tables list in `tests/test_rls.py` (confirm they're correctly absent from it, don't add them).
- Real-Postgres integration tests are gated on `TEST_DATABASE_URL`, matching the existing project convention (`backend/tests/conftest.py`'s `migrated_db` fixture).
- Before creating a new repository function, check whether an existing one already does the same query — Task 3 reuses `app.repositories.mailbox_connections.list_orgs_with_granted_mailbox_connections` (added by the earlier repository-extraction plan) rather than reimplementing it, which the ported reference implementation (predating that refactor) did not have available to reuse.

---

### Task 1: `BackgroundJob` model, `BackgroundJobStatus` enum, migration

**Files:**
- Create: `backend/app/models/background_job.py`
- Modify: `backend/app/models/enums.py` (add `BackgroundJobStatus`)
- Modify: `backend/app/models/__init__.py` (register the new model)
- Create: `backend/alembic/versions/0023_background_jobs.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `app.models.background_job.BackgroundJob` (ORM model), `app.models.enums.BackgroundJobStatus` — consumed by Tasks 2, 3, 4.

- [ ] **Step 1: Add the `BackgroundJobStatus` enum**

Add to `backend/app/models/enums.py` (at the end of the file):

```python
class BackgroundJobStatus(str, enum.Enum):
    """Lifecycle of a row in the worker work-queue (background_jobs). See
    app/services/jobs/queue.py."""

    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"
```

- [ ] **Step 2: Create the `BackgroundJob` model**

```python
# backend/app/models/background_job.py
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import BackgroundJobStatus
from app.models.mixins import TimestampMixin, UUIDPkMixin
from app.models.pg_enum import pg_enum


class BackgroundJob(UUIDPkMixin, TimestampMixin, Base):
    """A row in the worker work-queue (see app/services/jobs/queue.py).

    Deliberately NOT row-level-security scoped, and deliberately has NO
    organization_id column (the org id, where a job needs one, lives inside
    `payload`): this is worker-owned infrastructure, in the same category as
    update_check_state / source_ip_identities — operated by the worker across
    all tenants, not tenant data. Keeping organization_id out of the columns
    also keeps it clear of the RLS meta-guard in tests/test_rls.py.

    Claimed with `SELECT ... FOR UPDATE SKIP LOCKED` (app/repositories/jobs.py)
    so any number of worker replicas can drain it in parallel without ever
    double-processing a job. A partial UNIQUE index on
    `dedupe_key WHERE status IN ('pending','running')` (created in migration
    0023) makes re-enqueuing an already-queued job a no-op — e.g. the leader
    enqueues `mailbox_poll:<org>` every interval without piling up duplicates
    when workers are behind."""

    __tablename__ = "background_jobs"

    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULL means "no dedup" — the row is always its own distinct job.
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[BackgroundJobStatus] = mapped_column(
        pg_enum(BackgroundJobStatus, "background_job_status"),
        nullable=False,
        default=BackgroundJobStatus.pending,
    )
    # Earliest time this job may be claimed — used for scheduled delay and for
    # exponential backoff between retries.
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5, server_default="5")
    # Identifier of the worker replica currently holding the job (observability).
    locked_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
```

Check `backend/app/models/mixins.py` and `backend/app/models/pg_enum.py` exist with `UUIDPkMixin`/`TimestampMixin` and `pg_enum` respectively (they're used identically by every other model in this codebase — e.g. `app/models/update_check_state.py`) before writing this; do not invent alternate names if they differ from this snippet.

- [ ] **Step 3: Register the model**

Add `from app.models.background_job import BackgroundJob` to `backend/app/models/__init__.py`, in the same alphabetized/grouped style as the existing imports there (check the file first — it's a flat list of model imports so alembic's autogenerate and `Base.metadata` see every table).

- [ ] **Step 4: Write the migration**

Check the latest migration under `backend/alembic/versions/` first (`ls backend/alembic/versions/ | sort | tail -3`) to confirm `0022_sender_review_archived` is still the current head and `down_revision` below is correct — if a newer migration exists, update `down_revision` to match it instead.

```python
# backend/alembic/versions/0023_background_jobs.py
"""background_jobs work queue

Worker-owned work queue (see app/services/jobs/queue.py + app/models/background_job.py).
Deliberately NOT row-level-security scoped and with no organization_id column — it's
infrastructure operated by the worker across all tenants, same category as
update_check_state. DML on it is granted to dmarc_app automatically via the
ALTER DEFAULT PRIVILEGES set in 0001_initial_schema.py (owner-created tables).

Revision ID: 0023_background_jobs
Revises: 0022_sender_review_archived
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023_background_jobs"
down_revision = "0022_sender_review_archived"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    now = sa.func.now()

    background_job_status = postgresql.ENUM(
        "pending", "running", "done", "failed", name="background_job_status"
    )
    background_job_status.create(bind, checkfirst=True)
    background_job_status.create_type = False

    op.create_table(
        "background_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("dedupe_key", sa.String(255), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", background_job_status, nullable=False, server_default="pending"),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
        sa.Column("locked_by", sa.String(64), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )

    # Claim scan: WHERE status='pending' AND run_after<=now() ORDER BY run_after.
    op.create_index(
        "ix_background_jobs_pending",
        "background_jobs",
        ["run_after"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    # At most one active (pending/running) job per dedupe_key — re-enqueue is a no-op.
    op.create_index(
        "uq_background_jobs_dedupe",
        "background_jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_background_jobs_dedupe", table_name="background_jobs")
    op.drop_index("ix_background_jobs_pending", table_name="background_jobs")
    op.drop_table("background_jobs")
    op.execute("DROP TYPE IF EXISTS background_job_status")
```

- [ ] **Step 5: Run the migration against the test database and verify**

```bash
cd backend
export TEST_DATABASE_URL="postgresql+asyncpg://postgres:postgres@localhost:55435/dmarc_test"
alembic -x sqlalchemy.url="${TEST_DATABASE_URL/+asyncpg/}" upgrade head
```

Check how other migrations in this project are actually invoked against the test DB first (search `backend/tests/conftest.py`'s `migrated_db` fixture for the real command/mechanism — it may run migrations programmatically rather than via a raw `alembic upgrade head` CLI call) and use that same mechanism rather than assuming the command above is exactly right.

Expected: migration applies with no errors; `\d background_jobs` (via `psql`) shows the table with both indexes.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/background_job.py backend/app/models/enums.py backend/app/models/__init__.py backend/alembic/versions/0023_background_jobs.py
git commit -m "Add the background_jobs work-queue table and model"
```

---

### Task 2: `app/repositories/jobs.py` — the queue's raw queries

**Files:**
- Create: `backend/app/repositories/jobs.py`
- Test: `backend/tests/repositories/test_jobs.py`

**Interfaces:**
- Consumes: `app.models.background_job.BackgroundJob`, `app.models.enums.BackgroundJobStatus` (Task 1).
- Produces: `ClaimedJob` (frozen dataclass: `id: uuid.UUID`, `job_type: str`, `payload: dict`, `attempts: int`, `max_attempts: int`), `enqueue_job(db, job_type, payload=None, *, dedupe_key=None, run_after=None, max_attempts=5) -> None`, `claim_one_job(db, worker_id: str) -> ClaimedJob | None`, `complete_job(db, job_id: uuid.UUID) -> None`, `fail_job(db, job: ClaimedJob, error: str) -> None`, `reclaim_stalled_jobs(db, stale_seconds: int) -> int` — all consumed by Task 3's `app/services/jobs/queue.py`. **None of these commit** — see Global Constraints.

- [ ] **Step 1: Write the repository file**

```python
# backend/app/repositories/jobs.py
"""Queries backing the worker's Postgres work queue (background_jobs). See
app/services/jobs/queue.py for the orchestration (handler registry, the
claim-dispatch-complete loop) that calls these — none of the functions here
commit; the caller owns each short-lived session's transaction."""

import dataclasses
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.background_job import BackgroundJob
from app.models.enums import BackgroundJobStatus

_BACKOFF_BASE_SECONDS = 30
_BACKOFF_MAX_SECONDS = 3600
# The partial-unique-index predicate (migration 0023) — used both for ON
# CONFLICT inference on enqueue and kept here as the single source of that
# expression.
_ACTIVE_PREDICATE = text("status IN ('pending', 'running')")


@dataclasses.dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    job_type: str
    payload: dict
    attempts: int
    max_attempts: int


async def enqueue_job(
    db: AsyncSession,
    job_type: str,
    payload: dict | None = None,
    *,
    dedupe_key: str | None = None,
    run_after: datetime | None = None,
    max_attempts: int = 5,
) -> None:
    """Insert a job. When `dedupe_key` is set and an active (pending/running)
    job already carries it, this is a no-op (ON CONFLICT DO NOTHING against
    the partial unique index) — so the leader can enqueue the same recurring
    job every tick without piling up duplicates."""
    stmt = pg_insert(BackgroundJob).values(
        id=uuid.uuid4(),
        job_type=job_type,
        dedupe_key=dedupe_key,
        payload=payload or {},
        status=BackgroundJobStatus.pending.value,
        run_after=run_after or datetime.now(timezone.utc),
        max_attempts=max_attempts,
    )
    if dedupe_key is not None:
        stmt = stmt.on_conflict_do_nothing(index_elements=["dedupe_key"], index_where=_ACTIVE_PREDICATE)
    await db.execute(stmt)


async def claim_one_job(db: AsyncSession, worker_id: str) -> ClaimedJob | None:
    """Atomically claim the next runnable job. `FOR UPDATE SKIP LOCKED` means
    two workers racing here never take the same row — one gets it, the other
    skips to the next. Returns None when there's nothing runnable right now.
    Increments `attempts` on claim, so a job that keeps dying mid-run
    eventually fails out."""
    row = (
        await db.execute(
            text(
                """
                UPDATE background_jobs
                SET status = 'running', locked_by = :wid, locked_at = now(),
                    attempts = attempts + 1, updated_at = now()
                WHERE id = (
                    SELECT id FROM background_jobs
                    WHERE status = 'pending' AND run_after <= now()
                    ORDER BY run_after
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, job_type, payload, attempts, max_attempts
                """
            ),
            {"wid": worker_id},
        )
    ).mappings().first()
    if row is None:
        return None
    return ClaimedJob(
        id=row["id"],
        job_type=row["job_type"],
        payload=row["payload"] or {},
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
    )


async def complete_job(db: AsyncSession, job_id: uuid.UUID) -> None:
    await db.execute(
        text("UPDATE background_jobs SET status = 'done', last_error = NULL, updated_at = now() WHERE id = :id"),
        {"id": job_id},
    )


async def fail_job(db: AsyncSession, job: ClaimedJob, error: str) -> None:
    """Retry with exponential backoff until max_attempts, then park as failed."""
    if job.attempts >= job.max_attempts:
        await db.execute(
            text("UPDATE background_jobs SET status = 'failed', last_error = :e, updated_at = now() WHERE id = :id"),
            {"e": error[:2000], "id": job.id},
        )
    else:
        backoff = min(_BACKOFF_BASE_SECONDS * (2 ** (job.attempts - 1)), _BACKOFF_MAX_SECONDS)
        await db.execute(
            text(
                """
                UPDATE background_jobs
                SET status = 'pending', last_error = :e, locked_by = NULL, locked_at = NULL,
                    run_after = now() + make_interval(secs => :secs), updated_at = now()
                WHERE id = :id
                """
            ),
            {"e": error[:2000], "secs": backoff, "id": job.id},
        )


async def reclaim_stalled_jobs(db: AsyncSession, stale_seconds: int) -> int:
    """Return jobs stuck in 'running' past `stale_seconds` (their worker died
    mid-run) back to 'pending' so another worker retries them. Returns the
    count reset. `attempts` was already bumped at claim time, so a
    repeatedly-crashing job still fails out eventually."""
    result = await db.execute(
        text(
            """
            UPDATE background_jobs
            SET status = 'pending', locked_by = NULL, locked_at = NULL, updated_at = now()
            WHERE status = 'running' AND locked_at < now() - make_interval(secs => :secs)
            """
        ),
        {"secs": stale_seconds},
    )
    return result.rowcount or 0
```

- [ ] **Step 2: Write repository tests**

Check `backend/tests/repositories/test_admin_updates.py` first for this project's exact `owner_factory`/`api` fixture usage pattern and match it.

```python
# backend/tests/repositories/test_jobs.py
import uuid

from app.repositories.jobs import claim_one_job, complete_job, enqueue_job, fail_job, reclaim_stalled_jobs


async def test_enqueue_and_claim_one_job(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        await enqueue_job(db, "noop", {"x": 1})
        await db.commit()

    async with owner_factory() as db:
        job = await claim_one_job(db, "w1")
        await db.commit()
    assert job is not None
    assert job.job_type == "noop"
    assert job.payload == {"x": 1}
    assert job.attempts == 1


async def test_claim_one_job_returns_none_when_empty(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        assert await claim_one_job(db, "w1") is None


async def test_dedupe_key_blocks_duplicate_active_jobs(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        await enqueue_job(db, "sweep", dedupe_key=f"sweep:{uuid.uuid4()}")
        key = f"sweep:{uuid.uuid4()}"
        await enqueue_job(db, "sweep", dedupe_key=key)
        await enqueue_job(db, "sweep", dedupe_key=key)  # no-op — key already active
        await db.commit()
        job = await claim_one_job(db, "w1")
        await db.commit()
    assert job is not None
    async with owner_factory() as db:
        assert await claim_one_job(db, "w1") is None  # nothing else pending for this key


async def test_complete_job_marks_done(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        await enqueue_job(db, "noop")
        await db.commit()
        job = await claim_one_job(db, "w1")
        await complete_job(db, job.id)
        await db.commit()
        # Re-claiming finds nothing — the job is done, not pending.
        assert await claim_one_job(db, "w1") is None


async def test_fail_job_retries_then_parks_as_failed(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        await enqueue_job(db, "noop", max_attempts=1)
        await db.commit()
        job = await claim_one_job(db, "w1")
        assert job.attempts == 1
        await fail_job(db, job, "boom")
        await db.commit()
        # max_attempts=1 reached on the first attempt — parked as failed, not retried.
        assert await claim_one_job(db, "w1") is None


async def test_reclaim_stalled_jobs_returns_running_to_pending(api):
    from sqlalchemy import text

    _client, owner_factory = api
    async with owner_factory() as db:
        await enqueue_job(db, "noop")
        await db.commit()
        job = await claim_one_job(db, "w1")
        await db.commit()
        # Simulate a crashed worker: backdate the lock well past the threshold.
        await db.execute(
            text("UPDATE background_jobs SET locked_at = now() - make_interval(secs => 3600) WHERE id = :id"),
            {"id": job.id},
        )
        await db.commit()
        assert await reclaim_stalled_jobs(db, stale_seconds=1800) == 1
        await db.commit()
        reclaimed = await claim_one_job(db, "w2")
        await db.commit()
    assert reclaimed is not None
    assert reclaimed.id == job.id
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/repositories/test_jobs.py -v`
Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git add backend/app/repositories/jobs.py backend/tests/repositories/test_jobs.py
git commit -m "Add app/repositories/jobs.py — the work queue's raw queries"
```

---

### Task 3: `app/services/jobs/queue.py` (orchestration) + `app/services/jobs/leader.py` (leader election)

**Files:**
- Create: `backend/app/services/jobs/__init__.py`
- Create: `backend/app/services/jobs/queue.py`
- Create: `backend/app/services/jobs/leader.py`
- Modify: `backend/app/config.py` (add `leader_lock_key`, `leader_database_url`)
- Modify: `backend/tests/conftest.py` (add `app_db_url`/`app_sessionmaker` fixtures)
- Test: `backend/tests/services/jobs/__init__.py`, `backend/tests/services/jobs/test_queue.py`, `backend/tests/services/jobs/test_leader.py`

**Interfaces:**
- Consumes: `app.repositories.jobs.{ClaimedJob, enqueue_job, claim_one_job, complete_job, fail_job, reclaim_stalled_jobs}` (Task 2).
- Produces: `app.services.jobs.queue.{register_handler(job_type, handler), enqueue(db, job_type, payload=None, *, dedupe_key=None, run_after=None, max_attempts=5) -> None, claim_one(db, worker_id) -> ClaimedJob | None, complete(db, job_id) -> None, fail(db, job, error) -> None, reclaim_stalled(db, stale_seconds) -> int, process_next(worker_id: str) -> bool}`, `app.services.jobs.leader.LeaderLock(lock_key: int)` with `.is_leader`, `.try_acquire() -> bool`, `.release() -> None` — all consumed by Task 4's `app/workers/scheduler.py`.

- [ ] **Step 1: Add the two new settings**

Add to `backend/app/config.py`, in the same section-comment style already used for other settings groups (check the file first):

```python
    # Worker (app/workers/scheduler.py): the Postgres advisory-lock key the
    # leader is elected on. leader_database_url is the connection the leader's
    # advisory lock is held on — defaults to database_url, but if PgBouncer (or
    # any transaction-mode pooler) sits in front of database_url, set this to a
    # DIRECT (unpooled) Postgres connection string instead: pg_advisory_lock is
    # session-scoped, and transaction pooling would silently return the
    # connection to the pool between statements, breaking leadership.
    leader_lock_key: int = 0x59414454
    leader_database_url: str | None = None
```

- [ ] **Step 2: Write `app/services/jobs/leader.py`**

```python
# backend/app/services/jobs/__init__.py
```
(empty file)

```python
# backend/app/services/jobs/leader.py
"""Advisory-lock leader election for the worker.

Exactly one worker replica holds a session-level Postgres advisory lock and acts
as the scheduler leader (enqueues due work + runs the stalled-job reaper). If it
dies, its dedicated DB connection drops and Postgres releases the lock
automatically, so another replica acquires it on its next attempt — automatic
failover with no extra coordination service. The lock is held on its own
NullPool connection (not a pooled one) so its lifecycle is exactly the leader's
and it never starves the worker's normal connection pool.

Uses settings.leader_database_url (falling back to settings.database_url) —
see the setting's own docstring in app/config.py for why this must bypass a
transaction-mode connection pooler if one is in front of the app.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import settings

logger = logging.getLogger("worker.leader")

# Dedicated engine: each connect() is a real, standalone connection whose close
# (or process death) releases the advisory lock deterministically.
_engine = create_async_engine(settings.leader_database_url or settings.database_url, poolclass=NullPool)


class LeaderLock:
    """Best-effort leader election via pg_try_advisory_lock. Call try_acquire()
    on an interval; it returns the current leadership state."""

    def __init__(self, lock_key: int) -> None:
        self._lock_key = lock_key
        self._conn: AsyncConnection | None = None

    @property
    def is_leader(self) -> bool:
        return self._conn is not None

    async def try_acquire(self) -> bool:
        # Already leader: confirm the held connection is still alive; if it
        # dropped, relinquish so someone (maybe us, next tick) can re-elect.
        if self._conn is not None:
            try:
                await self._conn.execute(text("SELECT 1"))
                return True
            except Exception:
                logger.warning("leader connection lost — relinquishing leadership")
                await self._drop_connection()

        conn = await _engine.connect()
        try:
            acquired = (
                await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": self._lock_key})
            ).scalar()
        except Exception:
            await conn.close()
            raise
        if acquired:
            self._conn = conn
            logger.info("acquired scheduler leadership (advisory lock %s)", self._lock_key)
            return True
        await conn.close()
        return False

    async def _drop_connection(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass

    async def release(self) -> None:
        """Explicitly release (best-effort — process exit would release it anyway)."""
        if self._conn is not None:
            try:
                await self._conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": self._lock_key})
            except Exception:
                pass
        await self._drop_connection()
        logger.info("released scheduler leadership")
```

This file is ported verbatim (no repository-layer split — it's connection-lifecycle code, not data access, the same exclusion category as `app/db/rls.py`), except the engine's URL now reads `settings.leader_database_url or settings.database_url` where the original read `settings.database_url` directly (the original branch predates the PgBouncer-compatibility requirement).

- [ ] **Step 3: Write `app/services/jobs/queue.py`**

```python
# backend/app/services/jobs/queue.py
"""Orchestration for the worker's Postgres work queue: the job-handler
registry and the claim-dispatch-complete loop. The actual queue mechanics
(enqueue, claim, complete, fail, reclaim) live in app/repositories/jobs.py —
this module calls them and owns each short-lived session's commit."""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from app.db.session import async_session_factory
from app.repositories.jobs import ClaimedJob, claim_one_job, complete_job, enqueue_job, fail_job, reclaim_stalled_jobs

logger = logging.getLogger("worker.queue")

# job_type -> async handler(payload). Handlers must be idempotent / safe to run
# at-least-once (a claimed job whose worker dies mid-run is retried).
JobHandler = Callable[[dict], Awaitable[None]]
_HANDLERS: dict[str, JobHandler] = {}


def register_handler(job_type: str, handler: JobHandler) -> None:
    _HANDLERS[job_type] = handler


async def enqueue(
    db,
    job_type: str,
    payload: dict | None = None,
    *,
    dedupe_key: str | None = None,
    run_after: datetime | None = None,
    max_attempts: int = 5,
) -> None:
    await enqueue_job(db, job_type, payload, dedupe_key=dedupe_key, run_after=run_after, max_attempts=max_attempts)
    await db.commit()


async def claim_one(db, worker_id: str) -> ClaimedJob | None:
    job = await claim_one_job(db, worker_id)
    await db.commit()
    return job


async def complete(db, job_id) -> None:
    await complete_job(db, job_id)
    await db.commit()


async def fail(db, job: ClaimedJob, error: str) -> None:
    await fail_job(db, job, error)
    await db.commit()


async def reclaim_stalled(db, stale_seconds: int) -> int:
    n = await reclaim_stalled_jobs(db, stale_seconds)
    await db.commit()
    return n


async def process_next(worker_id: str) -> bool:
    """Claim + run the next job. Returns True if a job was handled (so the caller
    can loop again immediately), False if the queue was idle (caller should
    sleep). Each DB step uses its own short transaction so the (possibly long)
    handler never holds one open."""
    async with async_session_factory() as db:
        job = await claim_one(db, worker_id)
    if job is None:
        return False

    handler = _HANDLERS.get(job.job_type)
    if handler is None:
        async with async_session_factory() as db:
            await fail(db, job, f"no handler registered for job_type={job.job_type!r}")
        logger.error("no handler for job_type=%s (job %s)", job.job_type, job.id)
        return True

    try:
        await handler(job.payload)
    except Exception as exc:  # noqa: BLE001 — any handler error is a job failure, not a worker crash
        logger.exception("job %s (%s) failed", job.id, job.job_type)
        async with async_session_factory() as db:
            await fail(db, job, repr(exc))
        return True

    async with async_session_factory() as db:
        await complete(db, job.id)
    return True
```

- [ ] **Step 4: Add the shared test fixtures**

Read `backend/tests/conftest.py` in full first — this step adds two fixtures alongside the existing ones (e.g. `rls_sessions`), reusing whatever private URL-building helper (something like `_app_url()`) and imports (`create_async_engine`, `NullPool`, `text`, `async_sessionmaker`, `pytest_asyncio`) the file already has. Match its exact existing helper name rather than assuming `_app_url()` is correct if it's actually named differently.

```python
@pytest.fixture
def app_db_url(migrated_db) -> str:
    """The dmarc_app (non-owner) async URL — used by tests that build their own
    engine(s), e.g. leader-election contention."""
    return _app_url()


@pytest_asyncio.fixture
async def app_sessionmaker(migrated_db):
    """A sessionmaker on the non-owner dmarc_app role (the role the worker runs
    as), with the worker/infra tables emptied first. Lets a test open several
    concurrent sessions — e.g. to prove FOR UPDATE SKIP LOCKED never
    double-claims a job."""
    engine = create_async_engine(_app_url(), poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM background_jobs"))
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
```

(This plan's version of `app_sessionmaker` deletes only `background_jobs` — `rate_limit_hits` doesn't exist yet at this point in the plan; Task 5 will extend this fixture to also clear `rate_limit_hits` once that table exists.)

- [ ] **Step 5: Write `app/services/jobs/test_queue.py`**

```python
# backend/tests/services/jobs/__init__.py
```
(empty file)

```python
# backend/tests/services/jobs/test_queue.py
"""Integration tests for the queue orchestration layer (app/services/jobs/queue.py).

Run as the non-owner dmarc_app role against a real Postgres — proving the
concurrency behavior the worker actually relies on (FOR UPDATE SKIP LOCKED,
dedup, retry/backoff, stalled-job reclaim, handler dispatch). Gated on
TEST_DATABASE_URL.
"""

import asyncio

from sqlalchemy import text

from app.services.jobs import queue


async def _count(db, sql: str, **params) -> int:
    return (await db.execute(text(sql), params)).scalar_one()


async def test_claim_skip_locked_never_double_claims(app_sessionmaker):
    async with app_sessionmaker() as db:
        for i in range(5):
            await queue.enqueue(db, "noop", {"i": i})

    async def claim(worker_id: str):
        async with app_sessionmaker() as db:
            return await queue.claim_one(db, worker_id)

    # Five workers race for five jobs at once — each must get a distinct one.
    claimed = await asyncio.gather(*[claim(f"w{i}") for i in range(5)])
    ids = [c.id for c in claimed if c is not None]
    assert len(ids) == 5
    assert len(set(ids)) == 5  # no job claimed by two workers

    # Nothing left to claim.
    async with app_sessionmaker() as db:
        assert await queue.claim_one(db, "w") is None


async def test_dedupe_key_blocks_duplicate_active_jobs(app_sessionmaker):
    async with app_sessionmaker() as db:
        await queue.enqueue(db, "sweep", dedupe_key="sweep")
        await queue.enqueue(db, "sweep", dedupe_key="sweep")  # no-op
        assert await _count(db, "SELECT count(*) FROM background_jobs WHERE dedupe_key = 'sweep'") == 1

    # A running job still counts as active — re-enqueue stays a no-op.
    async with app_sessionmaker() as db:
        job = await queue.claim_one(db, "w")
        await queue.enqueue(db, "sweep", dedupe_key="sweep")
        assert await _count(db, "SELECT count(*) FROM background_jobs WHERE dedupe_key = 'sweep'") == 1

    # Once it's done, the dedupe slot frees up and a fresh one can be enqueued.
    async with app_sessionmaker() as db:
        await queue.complete(db, job.id)
        await queue.enqueue(db, "sweep", dedupe_key="sweep")
        assert await _count(db, "SELECT count(*) FROM background_jobs WHERE dedupe_key = 'sweep'") == 2
        assert await _count(
            db, "SELECT count(*) FROM background_jobs WHERE dedupe_key = 'sweep' AND status = 'pending'"
        ) == 1


async def test_fail_retries_with_backoff_then_fails_out(app_sessionmaker):
    async with app_sessionmaker() as db:
        await queue.enqueue(db, "noop", max_attempts=2)

    async with app_sessionmaker() as db:
        job = await queue.claim_one(db, "w")
        assert job.attempts == 1
        await queue.fail(db, job, "boom")
        row = (
            await db.execute(text("SELECT status, run_after > now() AS deferred FROM background_jobs WHERE id = :id"), {"id": job.id})
        ).mappings().one()
        assert row["status"] == "pending"
        assert row["deferred"] is True  # backoff pushed run_after into the future

    # Backoff means it isn't immediately claimable.
    async with app_sessionmaker() as db:
        assert await queue.claim_one(db, "w") is None

    # Clear the backoff, re-claim (attempts=2 == max), fail -> parked as failed.
    async with app_sessionmaker() as db:
        await db.execute(text("UPDATE background_jobs SET run_after = now() WHERE id = :id"), {"id": job.id})
        await db.commit()
        job2 = await queue.claim_one(db, "w")
        assert job2.attempts == 2
        await queue.fail(db, job2, "boom again")
        status = (await db.execute(text("SELECT status FROM background_jobs WHERE id = :id"), {"id": job.id})).scalar()
        assert status == "failed"


async def test_reclaim_stalled_returns_running_jobs_to_pending(app_sessionmaker):
    async with app_sessionmaker() as db:
        await queue.enqueue(db, "noop")
        job = await queue.claim_one(db, "w")  # -> running
        # Simulate a crashed worker: backdate the lock well past the threshold.
        await db.execute(
            text("UPDATE background_jobs SET locked_at = now() - make_interval(secs => 3600) WHERE id = :id"),
            {"id": job.id},
        )
        await db.commit()
        assert await queue.reclaim_stalled(db, stale_seconds=1800) == 1
        status = (await db.execute(text("SELECT status FROM background_jobs WHERE id = :id"), {"id": job.id})).scalar()
        assert status == "pending"

    # A fresh (recently-locked) running job is NOT reclaimed.
    async with app_sessionmaker() as db:
        await queue.enqueue(db, "noop")
        await queue.claim_one(db, "w")
        assert await queue.reclaim_stalled(db, stale_seconds=1800) == 0


async def test_process_next_runs_handler_and_completes(app_sessionmaker, monkeypatch):
    seen = []

    async def handler(payload: dict) -> None:
        seen.append(payload)

    queue.register_handler("test_ok", handler)
    monkeypatch.setattr(queue, "async_session_factory", app_sessionmaker)

    async with app_sessionmaker() as db:
        await queue.enqueue(db, "test_ok", {"x": 1})

    assert await queue.process_next("w") is True
    assert seen == [{"x": 1}]
    async with app_sessionmaker() as db:
        status = (await db.execute(text("SELECT status FROM background_jobs WHERE job_type = 'test_ok'"))).scalar()
    assert status == "done"

    # Idle queue -> False (caller should sleep).
    assert await queue.process_next("w") is False


async def test_process_next_failing_handler_is_recorded(app_sessionmaker, monkeypatch):
    async def bad(_payload: dict) -> None:
        raise RuntimeError("nope")

    queue.register_handler("test_bad", bad)
    monkeypatch.setattr(queue, "async_session_factory", app_sessionmaker)

    async with app_sessionmaker() as db:
        await queue.enqueue(db, "test_bad", max_attempts=1)

    assert await queue.process_next("w") is True
    async with app_sessionmaker() as db:
        row = (
            await db.execute(text("SELECT status, last_error FROM background_jobs WHERE job_type = 'test_bad'"))
        ).mappings().one()
    assert row["status"] == "failed"  # max_attempts=1 -> first failure parks it
    assert "nope" in row["last_error"]
```

- [ ] **Step 6: Write `app/services/jobs/test_leader.py`**

```python
# backend/tests/services/jobs/test_leader.py
"""Integration test for advisory-lock leader election (app/services/jobs/leader.py).

Two LeaderLock instances contend for the same key against a real Postgres; only
one may hold it, and it hands over after release. Gated on TEST_DATABASE_URL.
"""

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.services.jobs import leader

_TEST_KEY = 0x7E57  # isolated from the app's real leader_lock_key


async def test_only_one_leader_at_a_time(app_db_url, monkeypatch):
    engine = create_async_engine(app_db_url, poolclass=NullPool)
    monkeypatch.setattr(leader, "_engine", engine)
    a = leader.LeaderLock(_TEST_KEY)
    b = leader.LeaderLock(_TEST_KEY)
    try:
        assert await a.try_acquire() is True
        assert a.is_leader is True

        # b cannot take a lock a already holds.
        assert await b.try_acquire() is False
        assert b.is_leader is False

        # a re-confirming leadership is idempotent.
        assert await a.try_acquire() is True

        # After a releases, b can take over.
        await a.release()
        assert a.is_leader is False
        assert await b.try_acquire() is True
        assert b.is_leader is True
        await b.release()
    finally:
        await engine.dispose()
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/services/jobs/ tests/repositories/test_jobs.py -v`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add backend/app/services/jobs/ backend/app/config.py backend/tests/conftest.py backend/tests/services/jobs/
git commit -m "Add the queue orchestration layer and advisory-lock leader election"
```

---

### Task 4: `app/workers/scheduler.py` rewrite + `app/workers/health.py`

**Files:**
- Modify: `backend/app/workers/scheduler.py` (full rewrite)
- Create: `backend/app/workers/health.py`
- Modify: `backend/app/config.py` (add worker settings)
- Modify: `backend/requirements.txt` (drop `apscheduler`)
- Modify: `docker-compose.yml` (healthcheck + doc comment)
- Modify: `.env.example` (document new settings)

**Interfaces:**
- Consumes: `app.services.jobs.queue.{register_handler, process_next, enqueue, reclaim_stalled}` (Task 3), `app.services.jobs.leader.LeaderLock` (Task 3), `app.repositories.mailbox_connections.list_orgs_with_granted_mailbox_connections` (pre-existing).
- Produces: `app.workers.health.{heartbeat(*, is_leader=False) -> None, start_health_server(port) -> ThreadingHTTPServer}` — consumed only within `scheduler.py`.

- [ ] **Step 1: Add worker settings**

Add to `backend/app/config.py`:

```python
    # Worker (app/workers/scheduler.py): every replica runs this many concurrent
    # queue-consumer loops; the queue is polled this often when idle. The leader
    # reclaims jobs stuck "running" longer than worker_job_stale_seconds (a crashed
    # worker) — must exceed the longest expected job runtime. worker_health_port
    # serves the liveness endpoint (app/workers/health.py).
    worker_concurrency: int = 4
    worker_queue_poll_interval_seconds: float = 5.0
    worker_job_stale_seconds: int = 1800
    worker_health_port: int = 8080
```

- [ ] **Step 2: Write `app/workers/health.py`**

```python
# backend/app/workers/health.py
"""Minimal liveness endpoint for the headless `worker` process.

The worker has no HTTP server otherwise, so an orchestrator (an ACA liveness
probe, a compose healthcheck) has nothing to probe. This exposes `GET /health`
that reports OK only while the worker's async heartbeat has fired recently — so a
*hung* worker (event loop wedged, not just a crashed process) is detected and
recycled, not just one that exited.

Runs the HTTP server in a daemon thread, deliberately: if the asyncio loop is
wedged, a loop-based responder would wedge too and always look healthy. The
thread keeps answering, and the heartbeat (updated from the loop) goes stale —
which is exactly the signal we want. `heartbeat()` is driven by a small async
task (app/workers/scheduler.py), so it reflects event-loop liveness rather than
whether any single job happens to be mid-run.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Considered stale (unhealthy) if the loop hasn't beaten in this long. The
# heartbeat task beats every few seconds, so this is generous margin.
HEALTH_STALE_SECONDS = 60

_last_beat = time.monotonic()
_is_leader = False


def heartbeat(*, is_leader: bool = False) -> None:
    global _last_beat, _is_leader
    _last_beat = time.monotonic()
    _is_leader = is_leader


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") not in ("", "/health", "/healthz"):
            self.send_response(404)
            self.end_headers()
            return
        age = time.monotonic() - _last_beat
        healthy = age < HEALTH_STALE_SECONDS
        body = json.dumps(
            {"status": "ok" if healthy else "stale", "loop_age_seconds": round(age, 1), "leader": _is_leader}
        ).encode()
        self.send_response(200 if healthy else 503)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:  # silence per-request logging
        pass


def start_health_server(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    threading.Thread(target=server.serve_forever, name="worker-health", daemon=True).start()
    return server
```

- [ ] **Step 3: Rewrite `app/workers/scheduler.py`**

Note the one deliberate deviation from the reference implementation: `_list_pollable_orgs` is **not** reimplemented here — it's replaced by calling the already-existing `app.repositories.mailbox_connections.list_orgs_with_granted_mailbox_connections(db)` directly (added by the earlier repository-extraction plan, which the original `fe424bd` predates). Confirm its exact signature in `backend/app/repositories/mailbox_connections.py` before wiring it in — it should return an iterable of `(organization_id, entra_tenant_id)` tuples.

```python
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
```

Note: `_register_handlers` registers `"rate_limit_prune"` calling `prune_rate_limit_hits` from `app.services.auth.rate_limit` — that function doesn't exist yet at this point in the plan (Task 6 adds it). This import will fail until Task 6 lands. **This is acceptable within this plan's own sequencing** (tasks run in order, and Task 6 comes before this code is ever executed against a running worker) but means Task 4's own test run (Step 5 below) must account for it: if Task 4 is implemented and tested before Task 6 in strict sequence, temporarily comment out the `rate_limit_prune` registration line and its `_SINGLETON_INTERVALS` entry, run this task's tests, then restore both in Task 6 once `prune_rate_limit_hits` exists. State clearly in this task's commit message and report if this workaround was used.

- [ ] **Step 4: Drop the `apscheduler` dependency**

Remove the `apscheduler==3.10.4` line (and its `# Background jobs` comment) from `backend/requirements.txt`. Verify nothing else imports `apscheduler` first: `grep -rn apscheduler backend/app/`.

- [ ] **Step 5: Add the docker-compose healthcheck**

In `docker-compose.yml`, find the `worker` service block and add a `healthcheck` alongside its existing config (check the file first for the exact current block to edit in place):

```yaml
  # Runs background jobs off a Postgres work queue (app/workers/scheduler.py).
  # Safe to scale horizontally — `docker compose up -d --scale worker=3` — jobs
  # are claimed with FOR UPDATE SKIP LOCKED so replicas never double-process, and
  # exactly one replica auto-elects itself leader (Postgres advisory lock) to run
  # the schedule. If you also scale the `api`, set RATE_LIMIT_BACKEND=postgres in
  # .env so the auth rate limit stays correct across replicas.
  worker:
    ...
    # Liveness: the worker's health server (app/workers/health.py) reports 503 if
    # the event loop stalls, so a wedged (not just crashed) replica is restarted.
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8080/health', timeout=5)"]
      interval: 30s
      timeout: 6s
      retries: 3
      start_period: 20s
```

- [ ] **Step 6: Document the new settings in `.env.example`**

Append near the end of `.env.example` (check the file's existing style/grouping first):

```
# --- Scale-out (optional; sensible defaults, only set when scaling) ---
# The worker is safe to run as multiple replicas (docker compose up -d --scale
# worker=N) with no config change — jobs are claimed with SKIP LOCKED and one
# replica auto-elects itself leader. These tune it:
#   WORKER_CONCURRENCY               consumer loops per replica (default 4)
#   WORKER_QUEUE_POLL_INTERVAL_SECONDS  idle queue poll (default 5)
#   WORKER_JOB_STALE_SECONDS         reclaim a job stuck 'running' this long,
#                                    i.e. a crashed worker (default 1800; must
#                                    exceed your longest job)
#   WORKER_HEALTH_PORT               liveness endpoint port (default 8080)
# WORKER_CONCURRENCY=4
#
# If a transaction-mode connection pooler (e.g. PgBouncer) sits in front of
# DATABASE_URL, set LEADER_DATABASE_URL to a DIRECT (unpooled) Postgres
# connection string — the leader's advisory lock is session-scoped and would
# misbehave under transaction pooling. Leave unset (falls back to
# DATABASE_URL) if you aren't using a pooler.
# LEADER_DATABASE_URL=
```

- [ ] **Step 7: Run tests**

Run: `pytest tests/services/jobs/ tests/repositories/test_jobs.py -v`, then the full suite: `pytest -v`.
Expected: all pass. (`scheduler.py` and `health.py` themselves have no direct unit tests — they're the composition root, exercised by the multi-replica smoke check in Task 8 — but this step confirms nothing else broke and the module imports cleanly: `python -c "import app.workers.scheduler"`.)

- [ ] **Step 8: Commit**

```bash
git add backend/app/workers/scheduler.py backend/app/workers/health.py backend/app/config.py backend/requirements.txt docker-compose.yml .env.example
git commit -m "Rewrite the worker as a Postgres-queue consumer with leader-driven scheduling"
```

---

### Task 5: `RateLimitHit` model, migration, `app/repositories/rate_limits.py`

**Files:**
- Create: `backend/app/models/rate_limit_hit.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/0024_rate_limit_hits.py`
- Create: `backend/app/repositories/rate_limits.py`
- Modify: `backend/tests/conftest.py` (extend `app_sessionmaker` to also clear `rate_limit_hits`)
- Test: `backend/tests/repositories/test_rate_limits.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `app.models.rate_limit_hit.RateLimitHit`, `app.repositories.rate_limits.{acquire_bucket_lock(db, bucket) -> None, count_recent_hits(db, bucket, window_seconds) -> int, earliest_hit_retry_after(db, bucket, window_seconds) -> int | None, record_hit(db, bucket) -> None, prune_hits_older_than(db, max_age_seconds) -> None}` — consumed by Task 6.

- [ ] **Step 1: Create the `RateLimitHit` model**

This table is deliberately simple (no UUID PK, no timestamp mixin — just an append-only hit log), so it does **not** use `UUIDPkMixin`/`TimestampMixin` like every other model in this codebase. Check `app/models/mixins.py`'s `Base` import path and match it, but don't force the standard mixins onto a table shape that doesn't need them.

```python
# backend/app/models/rate_limit_hit.py
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class RateLimitHit(Base):
    """One row per rate-limited request, backing the optional Postgres shared
    rate-limit backend (RATE_LIMIT_BACKEND=postgres in
    app/services/auth/rate_limit.py) so the auth rate limit is correct across
    multiple api replicas. Worker/infra table: NOT row-level-security scoped,
    no organization_id column. Old rows are pruned by the rate_limit_prune
    background job (app/workers/scheduler.py)."""

    __tablename__ = "rate_limit_hits"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # "<limiter-name>:<client-key>" (e.g. "login:203.0.113.10").
    bucket: Mapped[str] = mapped_column(String(320), nullable=False)
    hit_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

Register it in `backend/app/models/__init__.py` alongside `BackgroundJob`.

- [ ] **Step 2: Write the migration**

```python
# backend/alembic/versions/0024_rate_limit_hits.py
"""rate_limit_hits — shared sliding-window store for multi-replica rate limiting

Backs the optional Postgres rate-limiter backend (RATE_LIMIT_BACKEND=postgres in
app/services/auth/rate_limit.py) so the auth rate limit is correct across multiple
api replicas, instead of each process counting only its own slice. Worker/infra
table: NOT row-level-security scoped and no organization_id column; DML is granted
to dmarc_app via the ALTER DEFAULT PRIVILEGES in 0001. Old rows are pruned by the
`rate_limit_prune` background job.

Revision ID: 0024_rate_limit_hits
Revises: 0023_background_jobs
"""

import sqlalchemy as sa
from alembic import op

revision = "0024_rate_limit_hits"
down_revision = "0023_background_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_hits",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("bucket", sa.String(320), nullable=False),
        sa.Column("hit_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_rate_limit_hits_bucket_hit_at", "rate_limit_hits", ["bucket", "hit_at"])


def downgrade() -> None:
    op.drop_index("ix_rate_limit_hits_bucket_hit_at", table_name="rate_limit_hits")
    op.drop_table("rate_limit_hits")
```

(This adds an explicit integer primary key, `id`, that the reference implementation's table didn't have — a bare append-only log with no PK is unusual for this codebase's conventions and makes individual-row operations harder for no benefit; every existing query pattern above works identically with or without it.)

- [ ] **Step 3: Write `app/repositories/rate_limits.py`**

```python
# backend/app/repositories/rate_limits.py
"""Queries backing the Postgres-shared rate-limit backend (rate_limit_hits).
See app/services/auth/rate_limit.py for orchestration (bucket-key building,
backend selection) that calls these."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def acquire_bucket_lock(db: AsyncSession, bucket: str) -> None:
    """Transaction-scoped advisory lock serializing concurrent checks for the
    same bucket across replicas, so the count-then-insert the caller performs
    next is exact rather than racy. Must be called inside the same
    transaction as that count/insert — releases automatically at transaction
    end (pg_advisory_xact_lock, not pg_advisory_lock)."""
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:b)::bigint)"), {"b": bucket})


async def count_recent_hits(db: AsyncSession, bucket: str, window_seconds: float) -> int:
    return (
        await db.execute(
            text(
                "SELECT count(*) FROM rate_limit_hits "
                "WHERE bucket = :b AND hit_at > now() - make_interval(secs => :w)"
            ),
            {"b": bucket, "w": window_seconds},
        )
    ).scalar_one()


async def earliest_hit_retry_after(db: AsyncSession, bucket: str, window_seconds: float) -> int | None:
    return (
        await db.execute(
            text(
                "SELECT ceil(extract(epoch FROM "
                "(make_interval(secs => :w) - (now() - min(hit_at)))))::int "
                "FROM rate_limit_hits WHERE bucket = :b AND hit_at > now() - make_interval(secs => :w)"
            ),
            {"b": bucket, "w": window_seconds},
        )
    ).scalar()


async def record_hit(db: AsyncSession, bucket: str) -> None:
    await db.execute(text("INSERT INTO rate_limit_hits (bucket, hit_at) VALUES (:b, now())"), {"b": bucket})


async def prune_hits_older_than(db: AsyncSession, max_age_seconds: int) -> None:
    await db.execute(
        text("DELETE FROM rate_limit_hits WHERE hit_at < now() - make_interval(secs => :s)"),
        {"s": max_age_seconds},
    )
```

- [ ] **Step 4: Extend the `app_sessionmaker` fixture**

In `backend/tests/conftest.py`, add `await conn.execute(text("DELETE FROM rate_limit_hits"))` alongside the existing `DELETE FROM background_jobs` line inside `app_sessionmaker` (added in Task 3, Step 4) so both worker/infra tables start clean for every test using this fixture.

- [ ] **Step 5: Write repository tests**

```python
# backend/tests/repositories/test_rate_limits.py
from app.repositories.rate_limits import count_recent_hits, prune_hits_older_than, record_hit


async def test_record_hit_and_count_recent_hits(api):
    _client, owner_factory = api
    async with owner_factory() as db:
        await record_hit(db, "test-bucket-1")
        await record_hit(db, "test-bucket-1")
        await db.commit()
        count = await count_recent_hits(db, "test-bucket-1", window_seconds=60)
    assert count == 2


async def test_count_recent_hits_excludes_old_hits(api):
    from sqlalchemy import text

    _client, owner_factory = api
    async with owner_factory() as db:
        await record_hit(db, "test-bucket-2")
        await db.commit()
        await db.execute(
            text("UPDATE rate_limit_hits SET hit_at = now() - make_interval(secs => 120) WHERE bucket = 'test-bucket-2'")
        )
        await db.commit()
        count = await count_recent_hits(db, "test-bucket-2", window_seconds=60)
    assert count == 0


async def test_prune_hits_older_than_removes_old_rows(api):
    from sqlalchemy import text

    _client, owner_factory = api
    async with owner_factory() as db:
        await record_hit(db, "test-bucket-3")
        await db.commit()
        await db.execute(
            text("UPDATE rate_limit_hits SET hit_at = now() - make_interval(secs => 7200) WHERE bucket = 'test-bucket-3'")
        )
        await db.commit()
        await prune_hits_older_than(db, max_age_seconds=3600)
        await db.commit()
        count = await count_recent_hits(db, "test-bucket-3", window_seconds=999999)
    assert count == 0
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/repositories/test_rate_limits.py -v`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add backend/app/models/rate_limit_hit.py backend/app/models/__init__.py backend/alembic/versions/0024_rate_limit_hits.py backend/app/repositories/rate_limits.py backend/tests/conftest.py backend/tests/repositories/test_rate_limits.py
git commit -m "Add the rate_limit_hits table and its repository"
```

---

### Task 6: Pluggable rate limiter (`RATE_LIMIT_BACKEND=postgres`)

**Files:**
- Modify: `backend/app/services/auth/rate_limit.py` (rewrite)
- Modify: `backend/app/config.py` (add `rate_limit_backend`)
- Modify: `backend/app/workers/scheduler.py` (uncomment the `rate_limit_prune` handler registration + `_SINGLETON_INTERVALS` entry, if Task 4 had to temporarily disable them)
- Test: `backend/tests/services/auth/__init__.py`, `backend/tests/services/auth/test_rate_limit.py`

**Interfaces:**
- Consumes: `app.repositories.rate_limits.{acquire_bucket_lock, count_recent_hits, earliest_hit_retry_after, record_hit, prune_hits_older_than}` (Task 5).
- Produces: nothing new for other tasks — `login_limiter`, `otp_limiter`, `rate_limiter`, `client_key` (all pre-existing names, unchanged signatures, so `app/routers/auth.py` and `app/routers/platform_admin.py` need **no changes**), plus `prune_rate_limit_hits() -> None` consumed by Task 4's `scheduler.py`.

- [ ] **Step 1: Add the setting**

Add to `backend/app/config.py`:

```python
    # Auth rate-limiter backend: "memory" (per-process, correct for a single api
    # container — the default) or "postgres" (shared across replicas). See
    # app/services/auth/rate_limit.py. Switch to "postgres" when running >1 api replica.
    rate_limit_backend: str = "memory"
```

- [ ] **Step 2: Rewrite `rate_limit.py`**

First, confirm every existing caller by re-running: `grep -rn "rate_limit\|login_limiter\|otp_limiter\|client_key" backend/app/routers/`. There should be exactly two callers (`app/routers/auth.py`, `app/routers/platform_admin.py`), each importing `login_limiter, otp_limiter, rate_limiter` and using `Depends(rate_limiter(login_limiter))` / `Depends(rate_limiter(otp_limiter))`. This rewrite keeps those four names (`login_limiter`, `otp_limiter`, `rate_limiter`, `client_key`) and their exact usage pattern unchanged — no router changes needed.

```python
# backend/app/services/auth/rate_limit.py
"""Per-client sliding-window rate limiter for the auth endpoints.

Two backends, chosen by `settings.rate_limit_backend`:

- **memory** (default) — a process-local sliding window. Correct when there's a
  single api process (one uvicorn worker per container, no `--workers`); state
  resets on restart, which only ever *forgives* counters, never wrongly blocks.
- **postgres** — a shared window in `rate_limit_hits`, so the limit is correct
  across multiple api replicas. Switch to this when scaling the api horizontally
  (see Production considerations in the README). Auth-endpoint volume is tiny, so
  the extra query per attempt is negligible — and cheaper than the Argon2 it gates.

Keyed on the real client IP, which is only trustworthy once the reverse-proxy
chain populates it correctly (FORWARDED_ALLOW_IPS, plus the edge's real-IP config).
Enforced as a pre-handler dependency, so it also caps how much Argon2 CPU a single
IP can burn (each login attempt is a deliberately expensive verify).
"""

import ipaddress
import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request, status

from app.config import settings
from app.db.session import async_session_factory
from app.repositories.rate_limits import (
    acquire_bucket_lock,
    count_recent_hits,
    earliest_hit_retry_after,
    prune_hits_older_than,
    record_hit,
)


class _InMemoryWindow:
    """Process-local sliding window (deque of hit timestamps per key)."""

    def __init__(self, *, max_events: int, window_seconds: float) -> None:
        self.max_events = max_events
        self.window_seconds = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, key: str) -> tuple[bool, int]:
        now = time.monotonic()
        with self._lock:
            cutoff = now - self.window_seconds
            bucket = self._hits[key]
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if not bucket:
                del self._hits[key]
                bucket = self._hits[key]
            if len(bucket) >= self.max_events:
                retry_after = int(self.window_seconds - (now - bucket[0])) + 1
                return False, max(retry_after, 1)
            bucket.append(now)
            return True, 0


async def _postgres_check(bucket: str, *, max_events: int, window_seconds: float) -> tuple[bool, int]:
    """Shared sliding window in `rate_limit_hits`. A transaction-scoped advisory
    lock on the bucket serializes concurrent checks for the same key across
    replicas, so the limit is exact rather than racy. All timing is done in the
    DB (no app/DB clock skew)."""
    async with async_session_factory() as db:
        async with db.begin():
            await acquire_bucket_lock(db, bucket)
            count = await count_recent_hits(db, bucket, window_seconds)
            if count >= max_events:
                retry_after = await earliest_hit_retry_after(db, bucket, window_seconds)
                return False, max(int(retry_after or 1), 1)
            await record_hit(db, bucket)
            return True, 0


class RateLimiter:
    """A named limiter that dispatches to the configured backend at call time
    (so RATE_LIMIT_BACKEND can be set per-deployment without code changes)."""

    def __init__(self, name: str, *, max_events: int, window_seconds: float) -> None:
        self.name = name
        self.max_events = max_events
        self.window_seconds = window_seconds
        self._memory = _InMemoryWindow(max_events=max_events, window_seconds=window_seconds)

    async def check(self, key: str) -> tuple[bool, int]:
        if settings.rate_limit_backend == "postgres":
            return await _postgres_check(
                f"{self.name}:{key}", max_events=self.max_events, window_seconds=self.window_seconds
            )
        return self._memory.check(key)


def client_key(request: Request) -> str:
    """Bucket key from the client IP — the full address for IPv4, the /64 for
    IPv6 (a single client controls a whole /64, so limiting per-address would be
    trivially evaded)."""
    host = request.client.host if request.client else "unknown"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if ip.version == 6:
        return str(ipaddress.ip_network(f"{host}/64", strict=False).network_address)
    return host


def rate_limiter(limiter: "RateLimiter"):
    """Build a FastAPI dependency enforcing `limiter` per client IP. Raises 429
    with a Retry-After header when the window is exhausted."""

    async def _dep(request: Request) -> None:
        allowed, retry_after = await limiter.check(client_key(request))
        if not allowed:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "too many attempts — please wait and try again",
                headers={"Retry-After": str(retry_after)},
            )

    return _dep


async def prune_rate_limit_hits(max_age_seconds: int = 86400) -> None:
    """Delete rate_limit_hits older than any window cares about — run periodically
    by the `rate_limit_prune` background job. No-op when the memory backend is
    used (the table is simply empty)."""
    async with async_session_factory() as db:
        await prune_hits_older_than(db, max_age_seconds)
        await db.commit()


# Module-level singletons so every request shares one limiter. Generous enough
# that no human trips them, tight enough to make online brute force impractical
# and to bound per-IP Argon2 CPU. login_limiter is shared by the local and
# platform-admin password endpoints; otp_limiter by both verify-otp endpoints.
login_limiter = RateLimiter("login", max_events=10, window_seconds=300)
otp_limiter = RateLimiter("otp", max_events=10, window_seconds=300)
```

Note this changes `.check()` on `_InMemoryWindow` from synchronous to still-synchronous (unchanged) but `RateLimiter.check()` is now `async` (it wasn't on the old `SlidingWindowLimiter`, whose `.check()` was sync) — confirm both call sites in `rate_limiter()`'s `_dep` already `await limiter.check(...)` (they do, shown above) and that no other code anywhere calls `.check()` synchronously (`grep -rn "\.check(" backend/app/routers/` — should show nothing, since `Depends(rate_limiter(...))` is the only caller).

- [ ] **Step 3: Re-enable the `rate_limit_prune` handler in `scheduler.py` if Task 4 disabled it**

If Task 4's Step 3 commented out the `queue.register_handler("rate_limit_prune", ...)` line and the `"rate_limit_prune": RATE_LIMIT_PRUNE_INTERVAL_SECONDS` entry in `_SINGLETON_INTERVALS` (because `prune_rate_limit_hits` didn't exist yet), restore both now — `app.services.auth.rate_limit.prune_rate_limit_hits` exists as of this task.

- [ ] **Step 4: Write tests**

```python
# backend/tests/services/auth/__init__.py
```
(empty file)

```python
# backend/tests/services/auth/test_rate_limit.py
"""Rate-limiter tests. The in-memory backend test needs no database (runs
everywhere); the Postgres shared-window test is gated on TEST_DATABASE_URL.
"""

from app.services.auth import rate_limit as rl


async def test_memory_backend_enforces_window():
    # Default backend is "memory"; no DB involved.
    limiter = rl.RateLimiter("mem", max_events=2, window_seconds=100)
    assert (await limiter.check("k"))[0] is True
    assert (await limiter.check("k"))[0] is True
    allowed, retry = await limiter.check("k")
    assert allowed is False
    assert retry >= 1
    # A different key has its own bucket.
    assert (await limiter.check("other"))[0] is True


async def test_postgres_backend_shares_window_across_sessions(app_sessionmaker, monkeypatch):
    # Each check opens its own session (as multiple api replicas would) — the
    # shared rate_limit_hits table must still enforce one window.
    monkeypatch.setattr(rl.settings, "rate_limit_backend", "postgres")
    monkeypatch.setattr(rl, "async_session_factory", app_sessionmaker)
    limiter = rl.RateLimiter("t", max_events=3, window_seconds=60)

    for _ in range(3):
        allowed, _ = await limiter.check("1.2.3.4")
        assert allowed is True
    allowed, retry = await limiter.check("1.2.3.4")
    assert allowed is False
    assert retry >= 1

    # Independent key, independent window.
    assert (await limiter.check("5.6.7.8"))[0] is True
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/services/auth/test_rate_limit.py -v`, then the full suite: `pytest -v`.
Expected: all pass — in particular, confirm `tests/routers/test_auth.py` and `tests/routers/test_platform_admin.py`'s existing rate-limit-dependent tests (login throttling) still pass unchanged, proving the router call sites needed no edits.

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/auth/rate_limit.py backend/app/config.py backend/app/workers/scheduler.py backend/tests/services/auth/
git commit -m "Make the auth rate limiter pluggable: in-memory default, Postgres-shared via RATE_LIMIT_BACKEND=postgres"
```

---

### Task 7: Optional read-replica routing for report/analytics queries

**Files:**
- Modify: `backend/app/db/session.py` (add the optional replica engine + `get_read_db`)
- Modify: `backend/app/config.py` (add `database_read_url`)
- Modify: `backend/app/routers/dmarc_reports.py` (switch 5 named GET endpoints to `Depends(get_read_db)`)
- Test: `backend/tests/test_read_replica_routing.py`

**Interfaces:**
- Consumes: nothing new from earlier tasks in this plan.
- Produces: `app.db.session.get_read_db` (FastAPI dependency, same shape as `get_db`) — consumed only within router files, this task's the only consumer.

- [ ] **Step 1: Add the setting**

Add to `backend/app/config.py`:

```python
    # Optional read-replica connection for report/analytics queries (see
    # app/db/session.py's get_read_db). Unset by default — every read goes to
    # the primary, exactly as today. Set to a read-only Postgres connection
    # string (e.g. a managed streaming replica) to offload latency-tolerant
    # dashboard/report reads there. Never used for anything a user might
    # expect to see their own just-made write reflected in.
    database_read_url: str | None = None
```

- [ ] **Step 2: Extend `db/session.py`**

```python
# backend/app/db/session.py
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
async_session_factory = async_sessionmaker(engine, expire_on_commit=False)

# Optional read-replica engine, built only if configured — falls back to the
# primary otherwise (see get_read_db below). A streaming replica physically
# rejects writes, so any accidental write through this session fails loudly
# with a clear Postgres error rather than silently succeeding unexpectedly.
_read_engine = create_async_engine(settings.database_read_url, pool_pre_ping=True) if settings.database_read_url else None
_read_session_factory = async_sessionmaker(_read_engine, expire_on_commit=False) if _read_engine else async_session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def get_read_db() -> AsyncGenerator[AsyncSession, None]:
    """Same shape as get_db, routed to the optional read-replica engine when
    DATABASE_READ_URL is configured, or the primary otherwise — invisible to
    a deployment with no replica configured. Used only by report/analytics GET
    endpoints where a brief replica-lag window is acceptable; anything where a
    user might expect to see their own just-made write reflected stays on
    get_db. RLS's SET LOCAL context-setting works identically on a replica
    session — it's the same schema and policies as the primary."""
    async with _read_session_factory() as session:
        yield session
```

- [ ] **Step 3: Wire it into the 5 named report/analytics endpoints**

In `backend/app/routers/dmarc_reports.py`, change `db: AsyncSession = Depends(get_db)` to `db: AsyncSession = Depends(get_read_db)` for exactly these 5 endpoint functions (confirm each by its route decorator before editing, since the file has many other GET endpoints that stay on `get_db`):

- `dmarc_reports_summary` — `GET /domains/{domain_id}/dmarc/reports/summary`
- `dmarc_reports_by_day` — `GET /domains/{domain_id}/dmarc/reports/by-day`
- `dmarc_reports_grouped` — `GET /domains/{domain_id}/dmarc/reports/grouped`
- `dmarc_trend` — `GET /dmarc/trend`
- `dmarc_posture` — `GET /dmarc/posture`

Update the `get_db` import line at the top of the file to also import `get_read_db` from the same module. Every other endpoint in this file (including `dmarc_summary`, `domain_rating`, `dmarc_sources`, `sender_inventory`, `dmarc_record_detail`, `unmatched_reports`, `detected_domains`, `dmarc_policy_builder`, and all POST/PATCH/DELETE endpoints) stays on `Depends(get_db)` unchanged.

- [ ] **Step 4: Write routing tests**

```python
# backend/tests/test_read_replica_routing.py
"""get_read_db falls back to the primary engine when DATABASE_READ_URL is
unset (the homelab default), and routes to a distinct engine when it is set.
Gated on TEST_DATABASE_URL — uses a second connection to the same test
Postgres to stand in for "a replica" (proving the routing logic itself,
not real streaming replication, which isn't practical to exercise in CI).
"""

import importlib

from sqlalchemy import text


async def test_get_read_db_falls_back_to_primary_when_unset(migrated_db):
    from app.db import session as session_module

    assert session_module._read_engine is None
    async for db in session_module.get_read_db():
        result = await db.execute(text("SELECT 1"))
        assert result.scalar_one() == 1
        break


async def test_get_read_db_uses_configured_replica_engine(migrated_db, monkeypatch, app_db_url):
    from app.db import session as session_module

    monkeypatch.setattr(session_module.settings, "database_read_url", app_db_url)
    importlib.reload(session_module)
    try:
        assert session_module._read_engine is not None
        async for db in session_module.get_read_db():
            result = await db.execute(text("SELECT 1"))
            assert result.scalar_one() == 1
            break
    finally:
        importlib.reload(session_module)  # restore module state for later tests
```

Check that `app_db_url` (added in Task 3, Step 4) is available at this point in the test run — it's a `conftest.py` fixture, so it is. If `importlib.reload` proves awkward against this project's actual module-caching behavior once you're implementing this, an equally valid alternative is constructing a second `create_async_engine`/`async_sessionmaker` pair directly in the test and monkeypatching `session_module._read_session_factory` to point at it, rather than reloading the module — use whichever is more reliable once you can actually run it, and note in your report which approach you used and why.

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_read_replica_routing.py -v`, then the full suite: `pytest -v`.
Expected: all pass, including every existing test in `tests/routers/test_dmarc_reports.py` that exercises the 5 switched endpoints (confirming the dependency swap didn't change response behavior — `get_read_db` falls back to the primary in every test environment where `DATABASE_READ_URL` is unset, which is every CI/dev environment by default).

- [ ] **Step 6: Commit**

```bash
git add backend/app/db/session.py backend/app/config.py backend/app/routers/dmarc_reports.py backend/tests/test_read_replica_routing.py
git commit -m "Add optional read-replica routing for report/analytics queries"
```

---

### Task 8: Final verification — README, multi-replica smoke check

**Files:**
- Modify: `README.md` ("Production considerations" section)

**Interfaces:**
- Consumes: everything from Tasks 1-7.
- Produces: nothing — documentation and verification only.

- [ ] **Step 1: Rewrite the README's "Production considerations" section**

Read the current section in full first (search for `## Production considerations`). Replace it with an updated version covering the worker queue, api rate-limiter backend, PgBouncer note, and read-replica note together — adapt this to whatever the section's current exact wording is rather than blindly overwriting, but ensure it covers all of:

```markdown
## Production considerations

A single `api` + single `worker` on one well-specced VM is plenty for one
operator or a handful of orgs, and is the default. When you need more, both tiers
scale horizontally without a message broker — just Postgres:

- **Scaling the `worker`.** Background work runs off a Postgres work queue
  (`background_jobs`): the leader enqueues due jobs, and any number of workers
  claim them with `SELECT … FOR UPDATE SKIP LOCKED`, so they never
  double-process. Run more with `docker compose up -d --scale worker=3` — heavy
  report ingestion then parallelizes across replicas. Exactly one replica
  auto-elects itself **leader** (a Postgres advisory lock) to run the schedule;
  if it dies another takes over automatically. Each worker also serves a liveness
  endpoint (`:8080/health`) that reports unhealthy if its loop stalls, so an
  orchestrator recycles a wedged — not just crashed — replica.
- **Scaling the `api`.** It's stateless (sessions live in Postgres), so it scales
  out freely — with one caveat: the auth rate limiter defaults to in-memory
  per-process. Running multiple `api` replicas? Set **`RATE_LIMIT_BACKEND=postgres`**
  so the limit is shared and correct across them ([rate_limit.py](backend/app/services/auth/rate_limit.py));
  it's dependency-free (reuses Postgres) and the auth-endpoint volume is tiny.
- **No Redis, no Celery — on purpose.** Reusing the datastore you already run and
  have hardened (RLS, encryption-at-rest, backups) keeps the attack surface and
  the ops burden down versus adding a broker. It comfortably handles this
  workload's cadence; the queue is an internal abstraction that could move to a
  broker later if a genuinely high-throughput need appeared.
- **Connection pooling (PgBouncer).** Point `DATABASE_URL` at a PgBouncer
  instance instead of Postgres directly once replica count makes raw connection
  count matter — no other app changes needed in the general case (RLS's
  `SET LOCAL` context-setting is transaction-scoped, already compatible with
  transaction-mode pooling). The one exception: set `LEADER_DATABASE_URL` to a
  **direct, unpooled** Postgres connection string, since the leader's advisory
  lock is session-scoped and would misbehave under transaction pooling.
- **Read replicas for report/analytics queries.** Set `DATABASE_READ_URL` to a
  read-only replica connection string to offload the report/trend/posture
  dashboard endpoints there — unset by default, so this is entirely opt-in.
  Anything where you'd expect to see your own just-made write reflected
  immediately stays on the primary.
- **RLS binds a per-connection role, not a per-request identity.** Tenant
  isolation is enforced by `SET LOCAL` GUCs inside each request's transaction
  (see [Multi-tenancy](#multi-tenancy)); every app connection is the same
  `dmarc_app` role, so correctness depends on the app always setting org context —
  which is why the [RLS test suite](#tests) exists to keep that guarantee honest.
- **Still single by default: Postgres and the resolver.** One Postgres (use a
  managed, backed-up instance in production, or add a read replica per above)
  and one DNSSEC-validating Unbound `resolver`. Size the host, or use a managed
  database, as you grow. Postgres sharding (Citus/Hyperscale) is a deliberately
  deferred future option, not built here — the RLS-by-organization_id data
  model already gives it a natural shard key if a single primary's vertical
  ceiling is ever genuinely the bottleneck.
```

- [ ] **Step 2: Live multi-replica smoke check**

This step is manual verification, not an automated test — run it once against a real Docker environment before considering this plan done, and record the observed result in your final report.

```bash
docker compose up -d --scale worker=3
docker compose logs worker --tail=50 | grep -i "acquired scheduler leadership"
```

Expected: exactly one of the three worker containers' logs shows "acquired scheduler leadership"; the other two show none. Then:

```bash
docker compose ps worker  # note which container is the leader from the logs above
docker kill <leader-container-name>
sleep 20
docker compose logs worker --tail=50 | grep -i "acquired scheduler leadership"
```

Expected: a different (surviving) worker container acquires leadership within ~15-30 seconds (matching `LEADER_TICK_SECONDS`), confirming automatic failover.

```bash
docker compose up -d --scale worker=3  # replace the killed container
pytest -v  # from backend/, full suite one more time on the final state
```

Expected: full suite passes.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "Document horizontal scale-out in the README's Production considerations"
```

---
