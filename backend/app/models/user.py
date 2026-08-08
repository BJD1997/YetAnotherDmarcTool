import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import AuthMethod, UserRole, UserStatus
from app.models.mixins import TimestampMixin, UUIDPkMixin
from app.models.pg_enum import pg_enum


class User(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("organization_id", "entra_object_id", name="uq_users_org_entra_object_id"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    auth_method: Mapped[AuthMethod] = mapped_column(
        pg_enum(AuthMethod, "auth_method"), nullable=False, default=AuthMethod.entra
    )

    # `oid` claim from the Entra ID token — stable per user per tenant/app.
    # Null for auth_method=local users (see the partial unique index on
    # lower(email) in the migration, which disambiguates local-auth login
    # lookups instead — Entra users are disambiguated by org+object_id).
    entra_object_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Only set for auth_method=local users. password_hash uses the same
    # argon2 hash_password/verify_password helpers PlatformAdmin already
    # does (app/services/auth/password.py). otp_enrolled_at gates whether
    # login can complete — TOTP is mandatory for local-auth users, so a
    # session is never issued until it's set (see app/routers/auth.py).
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    otp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    otp_enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole, "user_role"), nullable=False, default=UserRole.member
    )
    status: Mapped[UserStatus] = mapped_column(
        pg_enum(UserStatus, "user_status"), nullable=False, default=UserStatus.active
    )

    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
