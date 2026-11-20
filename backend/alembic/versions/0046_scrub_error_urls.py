"""Remove request URLs from stored poll errors

Errors saved before v0.3.0-beta23 hold httpx's raw text, including the
Graph delta link with its sync token. Rewrite them to the status only.

Revision ID: 0046_scrub_error_urls
Revises: 0045_google_mailbox
"""

from alembic import op

revision = "0046_scrub_error_urls"
down_revision = "0045_google_mailbox"
branch_labels = None
depends_on = None

_PATTERN = r"(Server|Client) error '([^']*)' for url '([^']*)'[\s\S]*"
_COLUMNS = (
    ("job_runs", "error_message"),
    ("mailbox_connections", "last_sync_error"),
    ("hosted_reports_poll_state", "last_sync_error"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.execute(
            f"""
            UPDATE {table} SET {column} = regexp_replace(
                {column}, $p${_PATTERN}$p$,
                CASE WHEN {column} LIKE '%graph.microsoft.com%' THEN 'Microsoft 365 answered HTTP \\2' ELSE 'Request failed: HTTP \\2' END
            )
            WHERE {column} ~ $p${_PATTERN}$p$
            """
        )


def downgrade() -> None:
    pass
