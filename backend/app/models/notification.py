import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPkMixin


class Notification(UUIDPkMixin, Base):
    """Something the reports turned up for the organization to act on — a
    domain not added yet, a DKIM selector not monitored yet. Produced and
    resolved by the notifications_refresh job (app/services/notifications);
    shown behind the bell to everyone in the organization."""

    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("organization_id", "kind", "subject_key", name="uq_notifications_org_kind_subject"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(40), nullable=False)  # domain_detected | dkim_selector_detected
    subject_key: Mapped[str] = mapped_column(String(400), nullable=False)
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="CASCADE"), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)  # handled | dismissed
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
