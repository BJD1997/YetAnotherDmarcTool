import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import JobStatus, JobType
from app.models.mixins import UUIDPkMixin
from app.models.pg_enum import pg_enum


class JobRun(UUIDPkMixin, Base):
    """Operational audit trail for both background job families (mailbox
    polling, DNS checks) — the source for basic failure/retry visibility and
    the operator-facing "recent runs" admin view. organization_id/domain_id
    are nullable since some runs (e.g. the DNS-check reconciliation sweep)
    aren't scoped to a single org."""

    __tablename__ = "job_runs"

    job_type: Mapped[JobType] = mapped_column(pg_enum(JobType, "job_type"), nullable=False)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="CASCADE"), nullable=True
    )

    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"), nullable=False, default=JobStatus.running
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
