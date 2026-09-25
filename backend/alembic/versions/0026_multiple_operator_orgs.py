"""allow multiple operator organizations

Drops 0004's "at most one operator org" partial unique index so a platform
admin can grant console access to more than one organization from the
admin UI (see platform_admin.update_organization).

Downgrade recreates the index, which fails if more than one organization is
still flagged — unset the extras first.

Revision ID: 0026_multiple_operator_orgs
Revises: 0025_pagination_keyset_indexes
"""

from alembic import op

revision = "0026_multiple_operator_orgs"
down_revision = "0025_pagination_keyset_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_organizations_single_operator")


def downgrade() -> None:
    op.execute(
        "CREATE UNIQUE INDEX uq_organizations_single_operator ON organizations (is_operator) WHERE is_operator"
    )
