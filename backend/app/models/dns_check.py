import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import CheckStatus, CheckType
from app.models.mixins import UUIDPkMixin
from app.models.pg_enum import pg_enum


class DnsCheckResult(UUIDPkMixin, Base):
    """One row per finding from a check run — a single check_type routinely
    produces several rows sharing the same (or no) subject, e.g. SPF's
    lookup-count finding and its 'all'-qualifier finding both have
    subject=NULL. "Current status" queries (see app/routers/dns_checks.py)
    fetch every row at MAX(checked_at) for the domain rather than a
    DISTINCT ON (domain_id, check_type, subject), since that would silently
    collapse same-subject findings from one run down to a single row."""

    __tablename__ = "dns_check_results"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    domain_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )

    check_type: Mapped[CheckType] = mapped_column(pg_enum(CheckType, "check_type"), nullable=False)
    dkim_selector_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("dkim_selectors.id", ondelete="CASCADE"), nullable=True
    )
    # e.g. the specific MX hostname a DANE/TLSA finding is about.
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)

    status: Mapped[CheckStatus] = mapped_column(pg_enum(CheckStatus, "check_status"), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Which version of the rule set produced this finding — lets the DMARCbis
    # module (still IETF-draft, semantics may shift) evolve without losing the
    # ability to tell which rows were evaluated under which ruleset.
    rule_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
