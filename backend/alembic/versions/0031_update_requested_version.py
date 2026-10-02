"""update_check_state.requested_version

The release an admin asked to install, read back by the Azure updater job
(GET /api/update-request) — see updater/azure_update.py.

Revision ID: 0031_update_requested_version
Revises: 0030_report_sender_check
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_update_requested_version"
down_revision = "0030_report_sender_check"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("update_check_state", sa.Column("requested_version", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("update_check_state", "requested_version")
