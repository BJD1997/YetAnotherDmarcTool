"""organizations.ask_ai_enabled: NULL = not answered yet

Org admins are asked once whether to turn Ask AI on; NULL means they haven't
answered. Organizations that already turned it on keep it; everyone else
(off by default in 0037, nobody had been asked) goes back to unanswered.

Revision ID: 0038_ask_ai_unanswered
Revises: 0037_ask_ai_enabled
"""

from alembic import op

revision = "0038_ask_ai_unanswered"
down_revision = "0037_ask_ai_enabled"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("organizations", "ask_ai_enabled", nullable=True, server_default=None)
    op.execute("UPDATE organizations SET ask_ai_enabled = NULL WHERE ask_ai_enabled IS FALSE")


def downgrade() -> None:
    op.execute("UPDATE organizations SET ask_ai_enabled = FALSE WHERE ask_ai_enabled IS NULL")
    op.alter_column("organizations", "ask_ai_enabled", nullable=False, server_default="false")
