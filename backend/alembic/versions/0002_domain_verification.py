"""domain ownership verification

Revision ID: 0002_domain_verification
Revises: 0001_initial_schema
Create Date: 2026-07-31

Adds a DNS TXT-challenge verification flow to domains, gating Phase 3's DNS
checks (a customer shouldn't see check results for a domain they haven't
proven they control) — see app/services/dns_checks/domain_verification.py.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_domain_verification"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    domain_verification_status = postgresql.ENUM(
        "pending", "verified", name="domain_verification_status"
    )
    domain_verification_status.create(bind, checkfirst=True)
    domain_verification_status.create_type = False

    op.add_column(
        "domains",
        sa.Column(
            "verification_status",
            domain_verification_status,
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column("domains", sa.Column("verification_token", sa.String(64), nullable=True))
    op.add_column("domains", sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))

    # Backfill any pre-existing rows with a random token (extension-free, migration-time
    # only — the application always generates real tokens via secrets.token_hex() on insert).
    op.execute(
        "UPDATE domains SET verification_token = md5(random()::text || clock_timestamp()::text) "
        "WHERE verification_token IS NULL"
    )
    op.alter_column("domains", "verification_token", nullable=False)
    op.create_unique_constraint("uq_domains_verification_token", "domains", ["verification_token"])


def downgrade() -> None:
    op.drop_constraint("uq_domains_verification_token", "domains", type_="unique")
    op.drop_column("domains", "verified_at")
    op.drop_column("domains", "verification_token")
    op.drop_column("domains", "verification_status")
    op.execute("DROP TYPE IF EXISTS domain_verification_status")
