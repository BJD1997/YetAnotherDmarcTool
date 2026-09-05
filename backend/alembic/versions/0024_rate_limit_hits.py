"""rate_limit_hits — shared sliding-window store for multi-replica rate limiting

Backs the optional Postgres rate-limiter backend (RATE_LIMIT_BACKEND=postgres in
app/services/auth/rate_limit.py) so the auth rate limit is correct across multiple
api replicas, instead of each process counting only its own slice. Worker/infra
table: NOT row-level-security scoped and no organization_id column; DML is granted
to dmarc_app via the ALTER DEFAULT PRIVILEGES in 0001. Old rows are pruned by the
`rate_limit_prune` background job.

Revision ID: 0024_rate_limit_hits
Revises: 0023_background_jobs
"""

import sqlalchemy as sa
from alembic import op

revision = "0024_rate_limit_hits"
down_revision = "0023_background_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limit_hits",
        # "<limiter-name>:<client-key>" (e.g. "login:203.0.113.10").
        sa.Column("bucket", sa.String(320), nullable=False),
        sa.Column("hit_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_rate_limit_hits_bucket_hit_at", "rate_limit_hits", ["bucket", "hit_at"])


def downgrade() -> None:
    op.drop_index("ix_rate_limit_hits_bucket_hit_at", table_name="rate_limit_hits")
    op.drop_table("rate_limit_hits")
