"""Carry minimal W3C trace context across durable task and outbox boundaries.

Revision ID: 20260925_007
Revises: 20260925_006
"""

from alembic import op

revision = "20260925_007"
down_revision = "20260925_006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("tasks", "outbox_events"):
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN traceparent text "
            "CHECK (traceparent ~ '^00-[0-9a-f]{32}-[0-9a-f]{16}-[0-9a-f]{2}$')"
        )


def downgrade() -> None:
    for table in ("outbox_events", "tasks"):
        op.execute(f"ALTER TABLE {table} DROP COLUMN traceparent")
