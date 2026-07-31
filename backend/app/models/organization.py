import uuid

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import OrganizationStatus
from app.models.mixins import TimestampMixin, UUIDPkMixin
from app.models.pg_enum import pg_enum


class Organization(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Entra (Azure AD) tenant ID this organization's users sign in with (dashboard
    # SSO) and whose mailbox we poll via Graph. Nullable until provisioning is
    # complete; once set, an Entra SSO login's `tid` claim is matched against it.
    entra_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), unique=True, nullable=True
    )

    status: Mapped[OrganizationStatus] = mapped_column(
        pg_enum(OrganizationStatus, "organization_status"),
        nullable=False,
        default=OrganizationStatus.pending_setup,
    )

    # At most one organization may have this set (enforced by a partial
    # unique index — see the migration) — its org_admins can access the
    # platform-admin API surface (/admin/*) through their normal Entra SSO
    # session, no separate local-auth login needed. The local platform_admin
    # login (see PlatformAdmin) stays available as a break-glass fallback.
    is_operator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
