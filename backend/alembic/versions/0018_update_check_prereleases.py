"""update check include prereleases

Revision ID: 0018_update_check_prereleases
Revises: 0017_update_check_state
Create Date: 2026-08-09

Moves the prerelease-channel opt-in from a static env var to a persisted
per-instance toggle an operator can flip from the Updates admin page
without editing .env/restarting containers.
"""

from alembic import op
import sqlalchemy as sa

revision = "0018_update_check_prereleases"
down_revision = "0017_update_check_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "update_check_state",
        sa.Column("include_prereleases", sa.Boolean, nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("update_check_state", "include_prereleases")
