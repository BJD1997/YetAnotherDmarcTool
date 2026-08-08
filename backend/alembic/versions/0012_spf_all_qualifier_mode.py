"""spf all qualifier mode

Revision ID: 0012_spf_all_qualifier_mode
Revises: 0011_hosted_report_address
Create Date: 2026-08-03

Per-organization toggle for the SPF checker's -all vs ~all recommendation
(strict: -all always preferred, today's behavior; conditional: prefer ~all
once a sending domain's own DMARC policy is already quarantine/reject) —
see app/services/dns_checks/spf.py.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012_spf_all_qualifier_mode"
down_revision = "0011_hosted_report_address"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    spf_all_qualifier_mode = postgresql.ENUM(
        "strict", "conditional", name="spf_all_qualifier_mode"
    )
    spf_all_qualifier_mode.create(bind, checkfirst=True)
    spf_all_qualifier_mode.create_type = False

    op.add_column(
        "organizations",
        sa.Column("spf_all_qualifier_mode", spf_all_qualifier_mode, nullable=False, server_default="strict"),
    )


def downgrade() -> None:
    op.drop_column("organizations", "spf_all_qualifier_mode")
    op.execute("DROP TYPE IF EXISTS spf_all_qualifier_mode")
