"""update check state

Revision ID: 0017_update_check_state
Revises: 0016_sign_in_events
Create Date: 2026-08-09

Single-row global state for app/services/update_check.py's periodic
GitHub releases check. Not org-scoped (a shared "what's the latest
version" fact, not tenant data), so no RLS here, same as
hosted_reports_poll_state.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0017_update_check_state"
down_revision = "0016_sign_in_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "update_check_state",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("latest_version", sa.String(64), nullable=True),
        sa.Column("latest_release_url", sa.String(512), nullable=True),
        sa.Column("latest_release_notes", sa.Text, nullable=True),
        sa.Column("latest_published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("check_error", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("update_check_state")
