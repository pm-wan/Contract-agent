"""Index reviewer authorization audit history pagination.

Revision ID: 20260927_021
Revises: 20260926_020
"""

from alembic import op

revision = "20260927_021"
down_revision = "20260926_020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX document_access_events_case_cursor_idx "
        "ON document_access_events (tenant_id, case_id, created_at DESC, id DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX document_access_events_case_cursor_idx")
