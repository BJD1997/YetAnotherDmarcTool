import secrets
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import DomainVerificationStatus
from app.models.mixins import TimestampMixin, UUIDPkMixin
from app.models.pg_enum import pg_enum


def _generate_verification_token() -> str:
    return secrets.token_hex(24)


class Domain(UUIDPkMixin, TimestampMixin, Base):
    __tablename__ = "domains"
    __table_args__ = (UniqueConstraint("organization_id", "name", name="uq_domains_org_name"),)

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )

    # Self-referential: null = apex/root domain, set = an explicitly-added
    # subdomain of another domain row in this same organization.
    parent_domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="CASCADE"), nullable=True
    )

    name: Mapped[str] = mapped_column(String(253), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Soft-archive rather than hard delete once a domain has report/check history.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Ownership proof via DNS TXT challenge (see app/services/dns_checks/domain_verification.py)
    # — required before Phase 3's DNS checks will run against a domain, so a
    # customer can't add (and see check results for) a domain they don't
    # control. A subdomain inherits its parent apex's verification at
    # creation time rather than needing its own separate token (see
    # create_domain in app/routers/domains.py).
    verification_status: Mapped[DomainVerificationStatus] = mapped_column(
        pg_enum(DomainVerificationStatus, "domain_verification_status"),
        nullable=False,
        default=DomainVerificationStatus.pending,
    )
    verification_token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=_generate_verification_token
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
