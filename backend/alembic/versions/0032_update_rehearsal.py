"""update_check_state.requested_rehearsal

Marks an update request as a rehearsal — the Azure updater job runs every
step on the version already running (see updater/azure_update.py).

Revision ID: 0032_update_rehearsal
Revises: 0031_update_requested_version
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_update_rehearsal"
down_revision = "0031_update_requested_version"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "update_check_state",
        sa.Column("requested_rehearsal", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("update_check_state", "requested_rehearsal")
