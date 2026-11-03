import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import TimestampMixin, UUIDPkMixin


class TrustedReporter(UUIDPkMixin, TimestampMixin, Base):
    """A report sender the org has said to believe, on top of the built-in
    sender_auth.KNOWN_REPORTERS. domain is the authenticated sending domain
    exactly as recorded in the reports' sender_domain column — not its
    organizational domain, so trusting dmarc.partner.example doesn't also
    trust every mailbox at partner.example."""

    __tablename__ = "trusted_reporters"
    __table_args__ = (UniqueConstraint("organization_id", "domain", name="uq_trusted_reporters_org_domain"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    trusted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
