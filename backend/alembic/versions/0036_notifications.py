"""notifications

Per-organization notifications for what the reports turn up (a domain not
added yet, a DKIM selector not monitored yet), produced and resolved by the
notifications_refresh worker job. Normal RLS.

Revision ID: 0036_notifications
Revises: 0035_domain_trends
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036_notifications"
down_revision = "0035_domain_trends"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("subject_key", sa.String(400), nullable=False),
        sa.Column(
            "domain_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("domains.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_reason", sa.String(20), nullable=True),
        sa.Column(
            "resolved_by", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
        ),
        sa.UniqueConstraint("organization_id", "kind", "subject_key", name="uq_notifications_org_kind_subject"),
        sa.CheckConstraint("kind IN ('domain_detected', 'dkim_selector_detected')", name="ck_notifications_kind"),
        sa.CheckConstraint(
            "resolved_reason IS NULL OR resolved_reason IN ('handled', 'dismissed')", name="ck_notifications_resolved_reason"
        ),
    )
    op.create_index("ix_notifications_org_resolved", "notifications", ["organization_id", "resolved_at"])

    op.execute("ALTER TABLE notifications ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notifications FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON notifications
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
    op.execute("DROP POLICY IF EXISTS tenant_isolation ON notifications")
    op.drop_table("notifications")
