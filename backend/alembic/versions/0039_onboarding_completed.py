"""organizations.onboarding_completed_at: the setup wizard was finished

Until now the wizard only showed while an organization had no mailbox or no
verified domain, so an admin who left it after verifying a domain never saw
the later steps (reporting address, Ask AI, waiting room). The wizard now
records when an admin reaches its last step; until then the Overview sends
org admins back to it.

Organizations that already receive reports count as finished, so nobody who
is in use gets sent back. One that is set up but still waiting for its first
report sees the remaining steps once.

Revision ID: 0039_onboarding_completed
Revises: 0038_ask_ai_unanswered
"""

import sqlalchemy as sa
from alembic import op

revision = "0039_onboarding_completed"
down_revision = "0038_ask_ai_unanswered"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("onboarding_completed_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        """
        UPDATE organizations o SET onboarding_completed_at = now()
        WHERE EXISTS (SELECT 1 FROM dmarc_aggregate_reports r WHERE r.organization_id = o.id)
        """
    )


def downgrade() -> None:
    op.drop_column("organizations", "onboarding_completed_at")
