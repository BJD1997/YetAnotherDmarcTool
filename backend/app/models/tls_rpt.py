import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import TlsRptPolicyType
from app.models.mixins import UUIDPkMixin
from app.models.pg_enum import pg_enum


class TlsRptReport(UUIDPkMixin, Base):
    """RFC 8460 SMTP TLS report — parsedmarc parses these too if they land in
    the same shared mailbox as the DMARC reports (Phase 2)."""

    __tablename__ = "tls_rpt_reports"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "policy_domain",
            "org_name",
            "date_range_begin",
            "date_range_end",
            name="uq_tls_rpt_reports_natural_key",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="SET NULL"), nullable=True
    )

    org_name: Mapped[str] = mapped_column(String(255), nullable=False)
    date_range_begin: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    policy_type: Mapped[TlsRptPolicyType] = mapped_column(
        pg_enum(TlsRptPolicyType, "tls_rpt_policy_type"), nullable=False
    )
    policy_domain: Mapped[str] = mapped_column(String(253), nullable=False)
    policy_string: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    summary_success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary_failure_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    source_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
