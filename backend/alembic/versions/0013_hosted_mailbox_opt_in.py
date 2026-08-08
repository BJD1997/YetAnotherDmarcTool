"""hosted mailbox opt in

Revision ID: 0013_hosted_mailbox_opt_in
Revises: 0012_spf_all_qualifier_mode
Create Date: 2026-08-05

Per-organization explicit opt-in to use the operator-hosted reporting
mailbox instead of (or alongside) their own MailboxConnection. Only
meaningful for Entra orgs — local-auth orgs (entra_tenant_id is None) have
no Entra tenant to grant Mail Access consent from, so hosted mailbox is
always available to them regardless of this column — see
app/routers/domains.py's _hosted_mailbox_available.
"""

from alembic import op
import sqlalchemy as sa

revision = "0013_hosted_mailbox_opt_in"
down_revision = "0012_spf_all_qualifier_mode"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column("hosted_mailbox_opt_in", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("organizations", "hosted_mailbox_opt_in")
