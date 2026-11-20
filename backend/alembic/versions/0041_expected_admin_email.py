"""organizations.expected_admin_email: who becomes org admin on first sign-in

Until now the first person of a Microsoft-linked organization to sign in
became its org admin, whoever that was. Now only the person whose email
matches the organization's expected admin email does; everyone else becomes
a member. Organizations without one stay without an admin until the platform
admin sets it or makes someone admin.

Revision ID: 0041_expected_admin_email
Revises: 0040_trusted_reporters
"""

import sqlalchemy as sa
from alembic import op

revision = "0041_expected_admin_email"
down_revision = "0040_trusted_reporters"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("organizations", sa.Column("expected_admin_email", sa.String(320), nullable=True))


def downgrade() -> None:
    op.drop_column("organizations", "expected_admin_email")
