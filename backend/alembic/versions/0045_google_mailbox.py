"""mailbox_connections: Google Workspace mailboxes

kind gains google: an organization's report mailbox in Google Workspace,
read through the server's service account (domain-wide delegation). The
mailbox address is the connection's mailbox_address.

Revision ID: 0045_google_mailbox
Revises: 0044_break_glass
"""

from alembic import op

revision = "0045_google_mailbox"
down_revision = "0044_break_glass"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE mailbox_kind ADD VALUE IF NOT EXISTS 'google'")


def downgrade() -> None:
    # Postgres can't drop enum values; Google connections go back to Microsoft
    # so nothing points at a kind the code no longer knows.
    op.execute("UPDATE mailbox_connections SET kind = 'microsoft', consent_status = 'revoked' WHERE kind = 'google'")
