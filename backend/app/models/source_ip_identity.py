from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import SourceMatchMethod
from app.models.pg_enum import pg_enum


class SourceIpIdentity(Base):
    """Cache of source_ip -> identified sending service, e.g. "Microsoft
    365"/"SMTP2GO", resolved via PTR + hostname pattern matching (see
    app/services/source_identification/). Deliberately NOT row-level-security
    scoped, same category as UserSession (app/models/session.py) — which IP
    belongs to which sending service isn't org-specific data at all, so a
    lookup here must work independent of any org context, and is shared
    across every org's dashboard rather than resolved redundantly per-org."""

    __tablename__ = "source_ip_identities"

    source_ip: Mapped[str] = mapped_column(INET, primary_key=True)
    ptr_hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Always populated (pattern label, PTR-derived registrable domain, or the
    # raw IP itself) so callers never need a null-check to get a display string.
    service_label: Mapped[str] = mapped_column(String(100), nullable=False)
    match_method: Mapped[SourceMatchMethod] = mapped_column(
        pg_enum(SourceMatchMethod, "source_match_method"), nullable=False
    )
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
