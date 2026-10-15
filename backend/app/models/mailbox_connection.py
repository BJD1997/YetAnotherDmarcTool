import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.enums import ConsentStatus, ImapSecurity, MailboxKind, SyncStatus
from app.models.mixins import TimestampMixin, UUIDPkMixin
from app.models.pg_enum import pg_enum
from app.services.auth.totp_secret import EncryptedSecret


class MailboxConnection(UUIDPkMixin, TimestampMixin, Base):
    """One shared mailbox per organization that all of its domains' DMARC/TLS-RPT
    reports land in. Populated/used starting Phase 2 (Graph ingestion); the
    schema exists from Phase 1 so organization provisioning can capture it."""

    __tablename__ = "mailbox_connections"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    mailbox_address: Mapped[str] = mapped_column(String(320), nullable=False)

    # Microsoft 365 (Graph) or any IMAP mailbox: one mailbox per organization.
    kind: Mapped[MailboxKind] = mapped_column(
        pg_enum(MailboxKind, "mailbox_kind"), nullable=False, default=MailboxKind.microsoft, server_default="microsoft"
    )
    imap_host: Mapped[str | None] = mapped_column(String(253), nullable=True)
    imap_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imap_security: Mapped[ImapSecurity | None] = mapped_column(pg_enum(ImapSecurity, "imap_security"), nullable=True)
    imap_username: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # Encrypted with FERNET_KEY; never sent back to the browser.
    imap_password: Mapped[str | None] = mapped_column(EncryptedSecret(1024), nullable=True)
    # Where reports are read from, and where they're moved once processed.
    imap_folder: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_processed_folder: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Only Authentication-Results headers with this ID (the mailbox's own mail
    # server) count for the report sender check; the detected one is offered
    # in Settings when it isn't confirmed yet. See sender_auth.
    imap_authserv_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    imap_detected_authserv_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

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
