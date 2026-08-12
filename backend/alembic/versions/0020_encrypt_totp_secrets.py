"""encrypt TOTP secrets at rest + widen otp_secret columns

Widens users.otp_secret / platform_admins.otp_secret from varchar(64) (only
ever big enough for the raw base32 secret) to varchar(255) to hold a Fernet
token, then encrypts any existing plaintext secrets in place.

The backfill runs as the migration (owner) role, so row-level security on
`users` doesn't hide rows here. It's idempotent: a value that already decrypts
as a Fernet token is left untouched, so a replay or a partially-migrated state
is safe. On a fresh install there are no rows to encrypt, so FERNET_KEY is only
actually required here when there's existing data to protect.

Revision ID: 0020_encrypt_totp_secrets
Revises: 0019_platform_admin_totp
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_encrypt_totp_secrets"
down_revision = "0019_platform_admin_totp"
branch_labels = None
depends_on = None

_TABLES = ("users", "platform_admins")


def upgrade() -> None:
    for table in _TABLES:
        op.alter_column(table, "otp_secret", type_=sa.String(255), existing_type=sa.String(64), existing_nullable=True)

    from app.services.crypto.secrets import decrypt_secret, encrypt_secret

    bind = op.get_bind()
    for table in _TABLES:
        rows = bind.execute(sa.text(f"SELECT id, otp_secret FROM {table} WHERE otp_secret IS NOT NULL")).fetchall()
        for row_id, secret in rows:
            try:
                decrypt_secret(secret.encode("ascii"))
                continue  # already a Fernet token — leave it
            except Exception:
                pass
            token = encrypt_secret(secret).decode("ascii")
            bind.execute(sa.text(f"UPDATE {table} SET otp_secret = :t WHERE id = :id"), {"t": token, "id": row_id})


def downgrade() -> None:
    # Decrypt back to plaintext base32 first so a downgraded instance still has
    # working secrets, then narrow the column again.
    from app.services.crypto.secrets import decrypt_secret

    bind = op.get_bind()
    for table in _TABLES:
        rows = bind.execute(sa.text(f"SELECT id, otp_secret FROM {table} WHERE otp_secret IS NOT NULL")).fetchall()
        for row_id, secret in rows:
            try:
                plain = decrypt_secret(secret.encode("ascii"))
            except Exception:
                continue  # already plaintext
            bind.execute(sa.text(f"UPDATE {table} SET otp_secret = :t WHERE id = :id"), {"t": plain, "id": row_id})

    for table in _TABLES:
        op.alter_column(table, "otp_secret", type_=sa.String(64), existing_type=sa.String(255), existing_nullable=True)
