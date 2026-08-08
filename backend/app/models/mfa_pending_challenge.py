import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPkMixin


class MfaPendingChallenge(UUIDPkMixin, Base):
    """The short-lived state between "password verified" and "a real
    session," for local-auth login (app/routers/auth.py). A leaked/guessed
    password alone can never produce a session without also passing the
    TOTP step this token gates. Same hashed-opaque-token lookup pattern as
    UserSession/PasswordSetupToken, not RLS-scoped for the same reason."""

    __tablename__ = "mfa_pending_challenges"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
