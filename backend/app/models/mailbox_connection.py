import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, LargeBinary, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import ConsentStatus, SyncStatus
from app.models.mixins import TimestampMixin, UUIDPkMixin
from app.models.pg_enum import pg_enum


class MailboxConnection(UUIDPkMixin, TimestampMixin, Base):
    """One shared mailbox per organization that all of its domains' DMARC/TLS-RPT
    reports land in. Populated/used starting Phase 2 (Graph ingestion); the
    schema exists from Phase 1 so organization provisioning can capture it."""

    __tablename__ = "mailbox_connections"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    mailbox_address: Mapped[str] = mapped_column(String(320), nullable=False)

    consent_status: Mapped[ConsentStatus] = mapped_column(
        pg_enum(ConsentStatus, "consent_status"),
        nullable=False,
        default=ConsentStatus.pending,
    )
    consent_granted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Graph delta-query cursor (see /users/{mailbox}/mailFolders/Inbox/messages/delta) —
    # lets polling resume without reprocessing already-seen messages.
    delta_link: Mapped[str | None] = mapped_column(Text, nullable=True)

    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_sync_status: Mapped[SyncStatus | None] = mapped_column(
        pg_enum(SyncStatus, "sync_status"), nullable=True
    )
    last_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Escape hatch: an org can bring its own Entra app registration instead of
    # using the operator's shared multi-tenant "Mail Access" app. Not exposed
    # in the UI yet (documented in the plan as a future option), but the
    # columns exist now so adding it later isn't a breaking migration.
    uses_custom_app: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    custom_client_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    custom_client_secret_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
