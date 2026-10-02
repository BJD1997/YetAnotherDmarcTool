import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DomainTrend(Base):
    """A domain's DMARC pass-rate trend (see app/services/rating/trend.py),
    one row per domain, replaced by each trend_refresh run."""

    __tablename__ = "domain_trends"

    domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    recent_pass_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_pass_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    recent_messages: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_messages: Mapped[int] = mapped_column(Integer, nullable=False)
    days_below: Mapped[int] = mapped_column(Integer, nullable=False)
    days_above: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
