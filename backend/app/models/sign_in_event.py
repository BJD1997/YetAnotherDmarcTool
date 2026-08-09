import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import AuthMethod, SignInResult
from app.models.mixins import UUIDPkMixin
from app.models.pg_enum import pg_enum


class SignInEvent(UUIDPkMixin, Base):
    """One row per completed sign-in attempt (success or failure) across all
    three auth flows — Entra SSO callback, local password+TOTP, TOTP
    enrollment. organization_id is nullable and RLS-scoped like JobRun's:
    some failures (e.g. no Organization matches the Entra tenant, or a
    local-login email that doesn't exist) have no org to attribute the row
    to, and are simply invisible to every org's own view — there's no
    useful org to show them under anyway. attempted_email is denormalized
    onto every row (not just failures) so the list endpoint never needs to
    join users, and it survives a later user deletion."""

    __tablename__ = "sign_in_events"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    # SET NULL, not CASCADE — this is a permanent record, not tied to the
    # user's continued existence the way a UserSession is.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    attempted_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_method: Mapped[AuthMethod] = mapped_column(pg_enum(AuthMethod, "auth_method"), nullable=False)
    result: Mapped[SignInResult] = mapped_column(pg_enum(SignInResult, "sign_in_result"), nullable=False)
    failure_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # Plain column, not server_default — set explicitly by record_sign_in_event
    # so the demo seed script can backdate rows.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
