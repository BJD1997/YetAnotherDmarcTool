"""dismissed detected domains

Revision ID: 0014_dismissed_detected_domains
Revises: 0013_hosted_mailbox_opt_in
Create Date: 2026-08-07

Org-scoped "not mine, stop suggesting it" list for GET /dmarc/detected-domains
— a header_from/policy_published name the org has explicitly dismissed rather
than registered. Normal RLS, same shape as sender_reviews (0008).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014_dismissed_detected_domains"
down_revision = "0013_hosted_mailbox_opt_in"
branch_labels = None
depends_on = None


def upgrade() -> None:
    now = sa.func.now()

    op.create_table(
        "dismissed_detected_domains",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(253), nullable=False),
        sa.Column(
            "dismissed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("organization_id", "name", name="uq_dismissed_detected_domains_org_name"),
    )
    op.create_index("ix_dismissed_detected_domains_organization_id", "dismissed_detected_domains", ["organization_id"])

    op.execute("ALTER TABLE dismissed_detected_domains ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE dismissed_detected_domains FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON dismissed_detected_domains
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
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON dismissed_detected_domains")
    op.drop_table("dismissed_detected_domains")
