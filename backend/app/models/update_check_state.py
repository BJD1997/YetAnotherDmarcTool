from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPkMixin


class UpdateCheckState(UUIDPkMixin, Base):
    """Single-row global state for update_check.py's periodic GitHub
    releases check — not org-scoped (it's one shared "what's the latest
    version of this app" fact, not tenant data), so this isn't
    RLS-protected, same as hosted_reports_poll_state."""

    __tablename__ = "update_check_state"

    latest_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latest_release_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    latest_release_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    check_error: Mapped[str | None] = mapped_column(Text, nullable=True)
