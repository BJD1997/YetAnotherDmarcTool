"""operator organization flag

Revision ID: 0004_operator_org
Revises: 0003_forensic_source_message_id
Create Date: 2026-07-31

Lets an organization's own org_admins manage the platform (provision other
orgs, etc.) through their normal Entra SSO session instead of requiring the
separate local platform_admin login for everything — see
app/middleware/tenant_context.py's dual-path get_current_platform_admin.
The local login stays available as a break-glass fallback.
"""

from alembic import op
import sqlalchemy as sa

revision = "0004_operator_org"
down_revision = "0003_forensic_source_message_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations", sa.Column("is_operator", sa.Boolean, nullable=False, server_default=sa.false())
    )
    # At most one operator org, enforced at the DB level (partial unique
    # index — a plain UNIQUE constraint on a boolean column would only allow
    # one row with EITHER value, not "at most one true").
    op.execute(
        "CREATE UNIQUE INDEX uq_organizations_single_operator ON organizations (is_operator) WHERE is_operator"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_organizations_single_operator")
    op.drop_column("organizations", "is_operator")
