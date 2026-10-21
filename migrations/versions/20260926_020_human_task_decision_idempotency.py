"""Make fact-review decisions safely replayable.

Revision ID: 20260926_020
Revises: 20260926_019
"""

from alembic import op

revision = "20260926_020"
down_revision = "20260926_019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE human_tasks ADD COLUMN decision_idempotency_key text")
    op.execute("ALTER TABLE human_tasks ADD COLUMN decision_request_hash text")
    op.execute(
        "ALTER TABLE human_tasks ADD CONSTRAINT human_task_decision_idempotency_check "
        "CHECK ((decision_idempotency_key IS NULL AND decision_request_hash IS NULL) OR "
        "(decision_idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$' AND "
        "decision_request_hash ~ '^[a-f0-9]{64}$'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE human_tasks DROP CONSTRAINT human_task_decision_idempotency_check")
    op.execute("ALTER TABLE human_tasks DROP COLUMN decision_request_hash")
    op.execute("ALTER TABLE human_tasks DROP COLUMN decision_idempotency_key")
