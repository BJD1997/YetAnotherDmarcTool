"""add fcrdns_valid to source_ip_identities

Adds a nullable BOOLEAN column recording forward-confirmed reverse DNS (FCrDNS)
for each cached source-IP identity: does the resolved PTR hostname point back to
the IP? No backfill runs here — existing rows keep fcrdns_valid = NULL, and
service_identifier.identify_many treats "has a ptr_hostname but NULL fcrdns_valid"
as a cache miss, so those rows are re-resolved (and forward-confirmed) live on
their next lookup rather than doing DNS from inside a migration. This table has
no row-level security (see app/models/source_ip_identity.py), so nothing else is
needed.

Revision ID: 0021_fcrdns_source_ip_identities
Revises: 0020_encrypt_totp_secrets
"""

import sqlalchemy as sa
from alembic import op

revision = "0021_fcrdns_source_ip_identities"
down_revision = "0020_encrypt_totp_secrets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("source_ip_identities", sa.Column("fcrdns_valid", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("source_ip_identities", "fcrdns_valid")
