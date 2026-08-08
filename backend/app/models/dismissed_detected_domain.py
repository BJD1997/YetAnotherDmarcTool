import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class DismissedDetectedDomain(UUIDPkMixin, TimestampMixin, Base):
    """A header_from/policy_published name the org has explicitly said isn't
    theirs to add — excluded from GET /dmarc/detected-domains going forward.
    Distinct from actually registering the domain (Domain model): this is
    for names that will never become one, e.g. spoofed lookalikes or noise
    from a sender the org doesn't recognize."""

    __tablename__ = "dismissed_detected_domains"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_dismissed_detected_domains_org_name"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(253), nullable=False)
    dismissed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
