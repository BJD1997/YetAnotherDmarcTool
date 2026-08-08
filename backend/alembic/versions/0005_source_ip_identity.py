"""source ip identity cache

Revision ID: 0005_source_ip_identity
Revises: 0004_operator_org
Create Date: 2026-07-31

Global cache of source_ip -> identified sending service (e.g. "Microsoft
365"), resolved via PTR + hostname pattern matching on read, not at
ingestion time (mirrors the existing offline=True decision in
parsedmarc_adapter.py). Deliberately NOT row-level-security scoped — same
category as user_sessions (see app/models/session.py and this table's own
model docstring): which service an IP belongs to isn't org-specific data.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005_source_ip_identity"
down_revision = "0004_operator_org"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    source_match_method = postgresql.ENUM(
        "pattern", "ptr_domain", "ip_fallback", name="source_match_method"
    )
    source_match_method.create(bind, checkfirst=True)
    source_match_method.create_type = False

    op.create_table(
        "source_ip_identities",
        sa.Column("source_ip", postgresql.INET, primary_key=True),
        sa.Column("ptr_hostname", sa.String(255), nullable=True),
        sa.Column("service_label", sa.String(100), nullable=False),
        sa.Column("match_method", source_match_method, nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("source_ip_identities")
    op.execute("DROP TYPE IF EXISTS source_match_method")
