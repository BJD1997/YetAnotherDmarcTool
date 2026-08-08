"""demo read only

Revision ID: 0015_demo_read_only
Revises: 0014_dismissed_detected_domains
Create Date: 2026-08-09

Flags an organization as read-only for every user in it — enforced by
enforce_demo_read_only in app/main.py, which blocks every state-changing
request for a flagged org's users. Defaults false so no existing org is
affected; the intended use is a published public demo login.
"""

from alembic import op
import sqlalchemy as sa

revision = "0015_demo_read_only"
down_revision = "0014_dismissed_detected_domains"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("is_demo_read_only", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("organizations", "is_demo_read_only")
