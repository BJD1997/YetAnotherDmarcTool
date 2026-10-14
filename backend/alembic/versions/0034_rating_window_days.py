"""organizations.rating_window_days

How far back each domain's grade, failing-message count, readiness and the
Senders list's default look: 30, 60, 90 (default) or 180 days, per
organization.

Revision ID: 0034_rating_window_days
Revises: 0033_check_status_pending
"""

import sqlalchemy as sa
from alembic import op

revision = "0034_rating_window_days"
down_revision = "0033_check_status_pending"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("rating_window_days", sa.Integer, nullable=False, server_default="90"))
    op.create_check_constraint(
        "ck_organizations_rating_window_days", "organizations", "rating_window_days IN (30, 60, 90, 180)"
    )


def downgrade() -> None:
    op.drop_constraint("ck_organizations_rating_window_days", "organizations", type_="check")
    op.drop_column("organizations", "rating_window_days")
