"""domain_trends

One row per domain: its DMARC pass-rate trend (last 7 days vs the 28
before), refreshed every 6 hours by the trend_refresh job. Normal RLS.

Revision ID: 0035_domain_trends
Revises: 0034_rating_window_days
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0035_domain_trends"
down_revision = "0034_rating_window_days"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "domain_trends",
        sa.Column(
            "domain_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("domains.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("recent_pass_pct", sa.Float, nullable=True),
        sa.Column("baseline_pass_pct", sa.Float, nullable=True),
        sa.Column("recent_messages", sa.Integer, nullable=False),
        sa.Column("baseline_messages", sa.Integer, nullable=False),
        sa.Column("days_below", sa.Integer, nullable=False),
        sa.Column("days_above", sa.Integer, nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("state IN ('up', 'stable', 'down', 'insufficient_data')", name="ck_domain_trends_state"),
    )
    op.create_index("ix_domain_trends_organization_id", "domain_trends", ["organization_id"])

    op.execute("ALTER TABLE domain_trends ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE domain_trends FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON domain_trends
        USING (
            organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            OR current_setting('app.is_platform_admin', true) = 'true'
        )
        WITH CHECK (
            organization_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid
            OR current_setting('app.is_platform_admin', true) = 'true'
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON domain_trends")
    op.drop_table("domain_trends")
