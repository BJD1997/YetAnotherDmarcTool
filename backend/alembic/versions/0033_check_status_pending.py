"""check_status: add 'pending'

STARTTLS from TLS-RPT reports reports "waiting for reports" as pending:
neither pass nor fail, and left out of the grade.

Revision ID: 0033_check_status_pending
Revises: 0032_update_rehearsal
"""

from alembic import op

revision = "0033_check_status_pending"
down_revision = "0032_update_rehearsal"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ADD VALUE can't run inside a transaction block on older Postgres.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE check_status ADD VALUE IF NOT EXISTS 'pending'")


def downgrade() -> None:
    # Postgres can't drop an enum value; leaving it is harmless.
    pass
