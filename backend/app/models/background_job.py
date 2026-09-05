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

    Claimed with `SELECT ... FOR UPDATE SKIP LOCKED` (queue.claim_one) so any
    number of worker replicas can drain it in parallel without ever
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
