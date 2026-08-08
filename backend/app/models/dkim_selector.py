import uuid

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class DkimSelector(UUIDPkMixin, TimestampMixin, Base):
    """DKIM selectors aren't discoverable via DNS alone, so they're registered
    manually per domain (Phase 3 builds the checker that uses these)."""

    __tablename__ = "dkim_selectors"
    __table_args__ = (UniqueConstraint("domain_id", "selector", name="uq_dkim_selectors_domain_selector"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )

    selector: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
