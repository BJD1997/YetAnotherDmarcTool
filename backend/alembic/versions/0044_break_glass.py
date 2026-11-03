"""Break-glass accounts, the mailbox's own Microsoft tenant, random sign-in links

users.is_break_glass marks each organization's one break-glass account:
local, org admin, and the only session that can choose or switch the
organization's sign-in provider. Backfill: the oldest active local org
admin. In organizations that already sign in through a provider, other
local accounts are disabled (the platform admin can re-enable or promote
one).

mailbox_connections.ms_tenant_id: a Microsoft 365 mailbox keeps its own
tenant, so the organization's sign-in can change without breaking report
ingestion. Copied from the organization for existing Microsoft mailboxes.

organizations.slug: replaced with a random name (sign-in links shouldn't be
guessable from organization names). Only v0.3.0 betas had slugs.

Revision ID: 0044_break_glass
Revises: 0043_sign_in_providers
"""

import secrets

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044_break_glass"
down_revision = "0043_sign_in_providers"
branch_labels = None
depends_on = None

_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"  # a copy of slug.random_slug's


def upgrade() -> None:
    op.add_column("users", sa.Column("is_break_glass", sa.Boolean, nullable=False, server_default="false"))
    op.create_index(
        "uq_users_one_break_glass_per_org", "users", ["organization_id"], unique=True,
        postgresql_where=sa.text("is_break_glass"),
    )
    op.execute(
        """
        UPDATE users SET is_break_glass = true
        WHERE id IN (
            SELECT DISTINCT ON (organization_id) id FROM users
            WHERE auth_method = 'local' AND role = 'org_admin' AND status = 'active'
            ORDER BY organization_id, created_at, id
        )
        """
    )
    op.execute(
        """
        UPDATE users SET status = 'disabled'
        FROM organizations o
        WHERE users.organization_id = o.id
          AND users.auth_method = 'local' AND NOT users.is_break_glass
          AND num_nonnulls(o.entra_tenant_id, o.google_workspace_domain, o.oidc_issuer) > 0
        """
    )

    op.add_column("mailbox_connections", sa.Column("ms_tenant_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute(
        """
        UPDATE mailbox_connections m SET ms_tenant_id = o.entra_tenant_id
        FROM organizations o
        WHERE m.organization_id = o.id AND m.kind = 'microsoft'
        """
    )

    bind = op.get_bind()
    taken: set[str] = set()
    for (org_id,) in bind.execute(sa.text("SELECT id FROM organizations")).all():
        slug = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        while slug in taken:
            slug = "".join(secrets.choice(_ALPHABET) for _ in range(10))
        taken.add(slug)
        bind.execute(sa.text("UPDATE organizations SET slug = :slug WHERE id = :id"), {"slug": slug, "id": org_id})


def downgrade() -> None:
    # Slugs stay random and disabled accounts stay disabled: both are safe.
    op.drop_column("mailbox_connections", "ms_tenant_id")
    op.drop_index("uq_users_one_break_glass_per_org", table_name="users")
    op.drop_column("users", "is_break_glass")
