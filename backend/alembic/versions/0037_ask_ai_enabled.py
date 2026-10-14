"""organizations.ask_ai_enabled

Opt-in per organization for the Ask AI buttons; off until an org admin
turns it on.

Revision ID: 0037_ask_ai_enabled
Revises: 0036_notifications
"""

import sqlalchemy as sa
from alembic import op

revision = "0037_ask_ai_enabled"
down_revision = "0036_notifications"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("ask_ai_enabled", sa.Boolean, nullable=False, server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("organizations", "ask_ai_enabled")
