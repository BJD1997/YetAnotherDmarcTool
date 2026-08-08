"""sender reviews

Revision ID: 0008_sender_reviews
Revises: 0007_remove_pending_setup_status
Create Date: 2026-08-01

Org-scoped (domain_id, service_label) approval/ownership record for the new
Overview page's sender-inventory workflow. Normal RLS, unlike the global
source_ip_identities cache (see that model's docstring) — which service an
IP belongs to isn't org data, but whether it's approved for a given domain
very much is.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008_sender_reviews"
down_revision = "0007_remove_pending_setup_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    sender_review_status = postgresql.ENUM(
        "pending", "approved", "ignored", "blocked", name="sender_review_status"
    )
    sender_review_status.create(bind, checkfirst=True)
    sender_review_status.create_type = False

    now = sa.func.now()

    op.create_table(
        "sender_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "domain_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("domains.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("service_label", sa.String(100), nullable=False),
        sa.Column("status", sender_review_status, nullable=False, server_default="pending"),
        sa.Column("owner", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column(
            "reviewed_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.UniqueConstraint("domain_id", "service_label", name="uq_sender_reviews_domain_service"),
    )
    op.create_index("ix_sender_reviews_organization_id", "sender_reviews", ["organization_id"])

    op.execute("ALTER TABLE sender_reviews ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sender_reviews FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON sender_reviews
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
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON sender_reviews")
    op.drop_table("sender_reviews")
    op.execute("DROP TYPE IF EXISTS sender_review_status")
