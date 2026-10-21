"""Make persisted task results append-only for the runtime database role.

Revision ID: 20260926_019
Revises: 20260926_018
"""

from alembic import op

revision = "20260926_019"
down_revision = "20260926_018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("REVOKE UPDATE, DELETE ON task_results FROM contract_runtime")


def downgrade() -> None:
    op.execute("GRANT UPDATE, DELETE ON task_results TO contract_runtime")
