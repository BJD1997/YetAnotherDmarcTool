import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import SenderReviewStatus
from app.models.mixins import TimestampMixin, UUIDPkMixin
from app.models.pg_enum import pg_enum


class SenderReview(UUIDPkMixin, TimestampMixin, Base):
    """Org-scoped approval/ownership record for a (domain, sending service)
    pair, e.g. "Microsoft 365 sending as mail.example.com: approved, owned by
    IT". Distinct from the global, non-RLS SourceIpIdentity cache (see
    app/models/source_ip_identity.py) — that table only identifies *what* a
    service is; this one tracks whether it's *approved for this domain*.
    Rows are created lazily (status=pending) the first time a service is
    seen for a domain (see the sender-inventory endpoint), which doubles as
    "new sender" detection via created_at, with no separate first-seen
    column needed."""

    __tablename__ = "sender_reviews"
    __table_args__ = (UniqueConstraint("domain_id", "service_label", name="uq_sender_reviews_domain_service"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )

    service_label: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[SenderReviewStatus] = mapped_column(
        pg_enum(SenderReviewStatus, "sender_review_status"), nullable=False, default=SenderReviewStatus.pending
    )
    owner: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
