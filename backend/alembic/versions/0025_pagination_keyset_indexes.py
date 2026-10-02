"""pagination keyset indexes

Adds two indexes that back the keyset-pagination ORDER BY / WHERE clauses
introduced by app/services/pagination.py's keyset_paginate (see
docs/superpowers/specs/2026-09-15-reusable-pagination-design.md). Both
tables already had rows queried without an index covering their sort
column, so every paginated request was sorting the full filtered set:

- tls_rpt_reports had ix_tls_rpt_reports_domain_id (see 0001) covering the
  domain_id filter, but nothing covering date_range_begin, the column
  /reports pages by (newest-first, descending).
- organizations had no index at all on name, the column Admin
  Organizations pages by (alphabetical, ascending).

Both are plain ascending composite indexes — Postgres serves an
ORDER BY ... DESC equally well via a backward index scan, so no explicit
DESC modifier is needed, matching this codebase's existing index style
(see e.g. ix_rate_limit_hits_bucket_hit_at in 0024).

Revision ID: 0025_pagination_keyset_indexes
Revises: 0024_rate_limit_hits
"""

from alembic import op

revision = "0025_pagination_keyset_indexes"
down_revision = "0024_rate_limit_hits"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_tls_rpt_reports_domain_id_date_range_begin_id",
        "tls_rpt_reports",
        ["domain_id", "date_range_begin", "id"],
    )
    op.create_index("ix_organizations_name_id", "organizations", ["name", "id"])


def downgrade() -> None:
    op.drop_index("ix_organizations_name_id", table_name="organizations")
    op.drop_index("ix_tls_rpt_reports_domain_id_date_range_begin_id", table_name="tls_rpt_reports")
