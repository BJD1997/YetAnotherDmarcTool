"""mailbox_connections: IMAP mailboxes besides Microsoft 365

An organization connects one mailbox of its own: Microsoft 365 (Graph, as
before) or any IMAP mailbox. kind says which; the imap_* columns hold the
IMAP server's settings, with the password encrypted like other secrets
(FERNET_KEY). Existing connections are Microsoft 365.

imap_authserv_id is how the report sender check trusts IMAP mail: only the
top Authentication-Results header carrying this ID (the mailbox's own mail
server, RFC 8601) is believed; anyone can add fake ones further down.

Revision ID: 0042_imap_mailbox
Revises: 0041_expected_admin_email
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0042_imap_mailbox"
down_revision = "0041_expected_admin_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    kind = postgresql.ENUM("microsoft", "imap", name="mailbox_kind")
    kind.create(op.get_bind(), checkfirst=True)
    kind.create_type = False
    security = postgresql.ENUM("ssl", "starttls", name="imap_security")
    security.create(op.get_bind(), checkfirst=True)
    security.create_type = False

    op.add_column("mailbox_connections", sa.Column("kind", kind, nullable=False, server_default="microsoft"))
    op.add_column("mailbox_connections", sa.Column("imap_host", sa.String(253), nullable=True))
    op.add_column("mailbox_connections", sa.Column("imap_port", sa.Integer, nullable=True))
    op.add_column("mailbox_connections", sa.Column("imap_security", security, nullable=True))
    op.add_column("mailbox_connections", sa.Column("imap_username", sa.String(320), nullable=True))
    op.add_column("mailbox_connections", sa.Column("imap_password", sa.String(1024), nullable=True))
    op.add_column("mailbox_connections", sa.Column("imap_folder", sa.String(255), nullable=True))
    op.add_column("mailbox_connections", sa.Column("imap_processed_folder", sa.String(255), nullable=True))
    # The ID the mailbox's own mail server signs its Authentication-Results
    # headers with: only those are believed for the report sender check.
    op.add_column("mailbox_connections", sa.Column("imap_authserv_id", sa.String(255), nullable=True))
    op.add_column("mailbox_connections", sa.Column("imap_detected_authserv_id", sa.String(255), nullable=True))


def downgrade() -> None:
    for column in (
        "imap_detected_authserv_id", "imap_authserv_id", "imap_processed_folder", "imap_folder", "imap_password", "imap_username", "imap_security", "imap_port",
        "imap_host", "kind",
    ):
        op.drop_column("mailbox_connections", column)
    op.execute("DROP TYPE IF EXISTS imap_security")
    op.execute("DROP TYPE IF EXISTS mailbox_kind")
