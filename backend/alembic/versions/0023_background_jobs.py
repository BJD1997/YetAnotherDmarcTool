"""background_jobs work queue

Worker-owned work queue (see app/services/jobs/queue.py + app/models/background_job.py).
Deliberately NOT row-level-security scoped and with no organization_id column — it's
infrastructure operated by the worker across all tenants, same category as
update_check_state. DML on it is granted to dmarc_app automatically via the
ALTER DEFAULT PRIVILEGES set in 0001_initial_schema.py (owner-created tables).

Revision ID: 0023_background_jobs
Revises: 0022_sender_review_archived
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0023_background_jobs"
down_revision = "0022_sender_review_archived"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    now = sa.func.now()

    background_job_status = postgresql.ENUM(
        "pending", "running", "done", "failed", name="background_job_status"
    )
    background_job_status.create(bind, checkfirst=True)
    background_job_status.create_type = False

    op.create_table(
        "background_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("dedupe_key", sa.String(255), nullable=True),
        sa.Column("payload", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", background_job_status, nullable=False, server_default="pending"),
        sa.Column("run_after", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
        sa.Column("locked_by", sa.String(64), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False),
    )

    # Claim scan: WHERE status='pending' AND run_after<=now() ORDER BY run_after.
    op.create_index(
        "ix_background_jobs_pending",
        "background_jobs",
        ["run_after"],
        postgresql_where=sa.text("status = 'pending'"),
    )
    # At most one active (pending/running) job per dedupe_key — re-enqueue is a no-op.
    op.create_index(
        "uq_background_jobs_dedupe",
        "background_jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_background_jobs_dedupe", table_name="background_jobs")
    op.drop_index("ix_background_jobs_pending", table_name="background_jobs")
    op.drop_table("background_jobs")
    op.execute("DROP TYPE IF EXISTS background_job_status")
