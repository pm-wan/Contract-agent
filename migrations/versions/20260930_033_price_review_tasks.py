"""Add auditable, non-adjudicating price review tasks.

Revision ID: 20260930_033
Revises: 20260930_032
"""

from alembic import op

revision = "20260930_033"
down_revision = "20260930_032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE human_tasks DROP CONSTRAINT human_tasks_type_check")
    op.execute(
        "ALTER TABLE human_tasks ADD CONSTRAINT human_tasks_type_check "
        "CHECK (type IN ('fact_review', 'price_review'))"
    )
    op.execute("ALTER TABLE human_tasks DROP CONSTRAINT human_tasks_decision_check")
    op.execute(
        "ALTER TABLE human_tasks ADD CONSTRAINT human_tasks_decision_check CHECK "
        "(decision IS NULL OR (type = 'fact_review' AND decision = 'confirm_fact') OR "
        "(type = 'price_review' AND decision IN ('record_review', 'request_material')))"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM human_tasks WHERE type = 'price_review' "
        "OR decision IN ('record_review', 'request_material')) THEN "
        "RAISE EXCEPTION 'cannot downgrade while price review tasks exist'; END IF; END $$"
    )
    op.execute("ALTER TABLE human_tasks DROP CONSTRAINT human_tasks_decision_check")
    op.execute(
        "ALTER TABLE human_tasks ADD CONSTRAINT human_tasks_decision_check "
        "CHECK (decision = 'confirm_fact')"
    )
    op.execute("ALTER TABLE human_tasks DROP CONSTRAINT human_tasks_type_check")
    op.execute(
        "ALTER TABLE human_tasks ADD CONSTRAINT human_tasks_type_check "
        "CHECK (type = 'fact_review')"
    )
