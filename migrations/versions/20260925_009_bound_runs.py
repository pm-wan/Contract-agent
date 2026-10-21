"""Bind production-path runs to an immutable input snapshot.

Revision ID: 20260925_009
Revises: 20260925_008
"""

from alembic import op

revision = "20260925_009"
down_revision = "20260925_008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE input_snapshots ADD CONSTRAINT input_snapshot_case_identity "
        "UNIQUE (tenant_id, case_id, id)"
    )
    op.execute("ALTER TABLE audit_runs ADD COLUMN input_snapshot_id uuid")
    op.execute("ALTER TABLE audit_runs ADD COLUMN started_by uuid")
    op.execute("ALTER TABLE audit_runs ADD COLUMN request_key text")
    op.execute(
        "ALTER TABLE audit_runs ADD CONSTRAINT bound_run_fields_together CHECK "
        "((input_snapshot_id IS NULL AND started_by IS NULL AND request_key IS NULL) OR "
        "(input_snapshot_id IS NOT NULL AND started_by IS NOT NULL AND request_key IS NOT NULL))"
    )
    op.execute(
        "ALTER TABLE audit_runs ADD CONSTRAINT bound_run_snapshot_fk FOREIGN KEY "
        "(tenant_id, case_id, input_snapshot_id) "
        "REFERENCES input_snapshots (tenant_id, case_id, id)"
    )
    op.execute(
        "ALTER TABLE audit_runs ADD CONSTRAINT bound_run_actor_fk FOREIGN KEY "
        "(tenant_id, case_id, started_by) REFERENCES case_members (tenant_id, case_id, actor_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX bound_run_request_key_idx ON audit_runs (tenant_id, request_key) "
        "WHERE request_key IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX bound_run_request_key_idx")
    op.execute("ALTER TABLE audit_runs DROP CONSTRAINT bound_run_actor_fk")
    op.execute("ALTER TABLE audit_runs DROP CONSTRAINT bound_run_snapshot_fk")
    op.execute("ALTER TABLE audit_runs DROP CONSTRAINT bound_run_fields_together")
    op.execute("ALTER TABLE audit_runs DROP COLUMN request_key")
    op.execute("ALTER TABLE audit_runs DROP COLUMN started_by")
    op.execute("ALTER TABLE audit_runs DROP COLUMN input_snapshot_id")
    op.execute("ALTER TABLE input_snapshots DROP CONSTRAINT input_snapshot_case_identity")
