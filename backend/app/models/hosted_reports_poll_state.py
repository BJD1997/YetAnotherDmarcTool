from datetime import datetime

from sqlalchemy import DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import SyncStatus
from app.models.mixins import UUIDPkMixin
from app.models.pg_enum import pg_enum


class HostedReportsPollState(UUIDPkMixin, Base):
    """Single-row global state for hosted_reports_poll_job.py's Graph
    delta-query cursor — not org-scoped (it's one shared operator mailbox
    across every customer using a hosted address, see Domain.
    hosted_report_address), so this isn't RLS-protected, same as
    platform_admins isn't. Mirrors MailboxConnection's delta_link/
    last_sync_* fields, just without an organization_id."""

    __tablename__ = "hosted_reports_poll_state"

    delta_link: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[SyncStatus | None] = mapped_column(pg_enum(SyncStatus, "sync_status"), nullable=True)
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
