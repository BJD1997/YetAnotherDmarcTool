"""starttls check type

Revision ID: 0006_starttls_check_type
Revises: 0005_source_ip_identity
Create Date: 2026-07-31

New CheckType value for the live STARTTLS prober (app/services/dns_checks/
starttls.py) backing the inbound-email per-MX-host table's TLS column —
distinct from DANE/MTA-STS, which are advisory/policy checks rather than a
live handshake probe.
"""

from alembic import op

revision = "0006_starttls_check_type"
down_revision = "0005_source_ip_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Safe inside a transaction on PG12+, as long as the new value isn't
    # used in the same transaction (it isn't — this migration only adds the
    # label, nothing inserts a 'starttls' row here).
    op.execute("ALTER TYPE check_type ADD VALUE IF NOT EXISTS 'starttls'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE — removing an enum label
    # requires rebuilding the type, not attempted here since this migration
    # only adds a label and never uses it to write data that would need
    # cleaning up first.
    pass
