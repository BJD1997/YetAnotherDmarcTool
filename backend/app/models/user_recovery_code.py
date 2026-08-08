import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPkMixin


class UserRecoveryCode(UUIDPkMixin, Base):
    """One-time TOTP-recovery backup codes, generated as a batch at
    enrollment (see app/services/auth/totp.py's generate_recovery_codes).
    Only the SHA-256 hash is ever persisted — the plaintext is shown to the
    user exactly once, at generation time, and cannot be retrieved again."""

    __tablename__ = "user_recovery_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
