import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPkMixin


class PlatformAdminMfaPendingChallenge(UUIDPkMixin, Base):
    """Platform-admin equivalent of MfaPendingChallenge — kept as a
    separate table (own FK target, own cookie) rather than reusing that
    one, since PlatformAdmin and User are different principal types with
    different id spaces."""

    __tablename__ = "platform_admin_mfa_pending_challenges"

    platform_admin_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("platform_admins.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
