"""Distinguish cancelled and superseded work.

Revision ID: 20260925_005
Revises: 20260925_004
"""

from alembic import op

revision = "20260925_005"
down_revision = "20260925_004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE audit_runs DROP CONSTRAINT audit_runs_status_check")
    op.execute(
        "ALTER TABLE audit_runs ADD CONSTRAINT audit_runs_status_check CHECK "
        "(status IN ('queued', 'running', 'waiting', 'completed', 'failed', "
        "'cancelled', 'superseded'))"
    )
    op.execute("ALTER TABLE tasks DROP CONSTRAINT tasks_status_check")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT tasks_status_check CHECK "
        "(status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'superseded'))"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM audit_runs WHERE status = 'superseded') "
        "OR EXISTS (SELECT 1 FROM tasks WHERE status = 'superseded') THEN "
        "RAISE EXCEPTION 'cannot downgrade while superseded work exists'; END IF; END $$"
    )
    op.execute("ALTER TABLE tasks DROP CONSTRAINT tasks_status_check")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT tasks_status_check CHECK "
        "(status IN ('queued', 'running', 'completed', 'failed', 'cancelled'))"
    )
    op.execute("ALTER TABLE audit_runs DROP CONSTRAINT audit_runs_status_check")
    op.execute(
        "ALTER TABLE audit_runs ADD CONSTRAINT audit_runs_status_check CHECK "
        "(status IN ('queued', 'running', 'waiting', 'completed', 'failed', 'cancelled'))"
    )
