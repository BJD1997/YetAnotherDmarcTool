import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import INET, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.mixins import UUIDPkMixin


class DmarcForensicReport(UUIDPkMixin, Base):
    """A single RUF (forensic) report (RFC 6591 fields). `raw_message` can
    contain fragments of an actual failing message — potential PII — and is
    nulled out by the retention job (Phase 4) 30 days after ingestion; the
    structured metadata is kept indefinitely for analytics.

    Unlike aggregate/TLS-RPT reports, RUF has no natural per-report dedup key
    of its own (repeated genuine failures can legitimately share the same
    arrival_date/source_ip/reported_domain) — source_message_id (the Graph
    message id) is the dedup key instead. This matters because Graph's delta
    query only yields a resume token per *page*, not per message, so a poll
    run that fails partway through can be retried and re-deliver messages
    already written in the failed attempt.
    """

    __tablename__ = "dmarc_forensic_reports"
    __table_args__ = (
        UniqueConstraint("organization_id", "source_message_id", name="uq_dmarc_forensic_reports_source_message"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="SET NULL"), nullable=True
    )

    arrival_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_ip: Mapped[str | None] = mapped_column(INET, nullable=True)

    reported_domain: Mapped[str] = mapped_column(String(253), nullable=False)
    original_envelope_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    dkim_domain: Mapped[str | None] = mapped_column(String(253), nullable=True)
    spf_dns: Mapped[str | None] = mapped_column(String(253), nullable=True)
    authentication_results: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Purge-able independently of the row (see retention job) — do not add a
    # NOT NULL constraint here even though it's usually populated at ingestion.
    raw_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_message_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
