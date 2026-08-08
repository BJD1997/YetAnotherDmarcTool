import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import AuthResult, Disposition
from app.models.mixins import TimestampMixin, UUIDPkMixin
from app.models.pg_enum import pg_enum


class DmarcAggregateReport(UUIDPkMixin, TimestampMixin, Base):
    """A single RUA (aggregate) report, one per reporter per reporting-domain
    per period. Populated by the Phase 2 ingestion pipeline (parsedmarc)."""

    __tablename__ = "dmarc_aggregate_reports"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "org_name",
            "report_id",
            "policy_published_domain",
            name="uq_dmarc_aggregate_reports_natural_key",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    # Nullable: a report whose policy_published_domain didn't match any
    # registered Domain is still persisted (see domain_matcher, Phase 2) and
    # surfaced in an "unmatched reports" admin view rather than dropped.
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="SET NULL"), nullable=True
    )

    report_id: Mapped[str] = mapped_column(String(255), nullable=False)
    org_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    date_range_begin: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    date_range_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Snapshot of the published DMARC policy at report time, kept alongside
    # (not instead of) the live DNS check for historical/audit value.
    policy_published_domain: Mapped[str] = mapped_column(String(253), nullable=False)
    policy_p: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_sp: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_pct: Mapped[int | None] = mapped_column(Integer, nullable=True)
    policy_adkim: Mapped[str | None] = mapped_column(String(8), nullable=True)
    policy_aspf: Mapped[str | None] = mapped_column(String(8), nullable=True)

    source_message_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DmarcAggregateRecord(UUIDPkMixin, Base):
    """One <record> row within an aggregate report — per-source-IP volume and
    auth results. Denormalizes organization_id/domain_id from the parent
    report so RLS and hot "volume over time by source" queries don't need a
    join."""

    __tablename__ = "dmarc_aggregate_records"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dmarc_aggregate_reports.id", ondelete="CASCADE"), nullable=False
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="SET NULL"), nullable=True
    )

    source_ip: Mapped[str] = mapped_column(INET, nullable=False)
    count: Mapped[int] = mapped_column(Integer, nullable=False)

    disposition: Mapped[Disposition] = mapped_column(pg_enum(Disposition, "disposition"), nullable=False)
    dkim_result: Mapped[AuthResult] = mapped_column(pg_enum(AuthResult, "auth_result"), nullable=False)
    spf_result: Mapped[AuthResult] = mapped_column(pg_enum(AuthResult, "auth_result"), nullable=False)

    header_from: Mapped[str] = mapped_column(String(253), nullable=False)
    envelope_from: Mapped[str | None] = mapped_column(String(253), nullable=True)
    envelope_to: Mapped[str | None] = mapped_column(String(253), nullable=True)

    # Variable-length list of per-mechanism {domain, selector, result} /
    # {domain, scope, result} entries — JSONB avoids a sprawling child table.
    auth_results: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    policy_evaluated_reasons: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
