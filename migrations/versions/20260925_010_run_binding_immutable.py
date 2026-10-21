"""Prevent a run from being rebound after its creation.

Revision ID: 20260925_010
Revises: 20260925_009
"""

from alembic import op

revision = "20260925_010"
down_revision = "20260925_009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_run_rebinding() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
             OR NEW.id IS DISTINCT FROM OLD.id
             OR NEW.case_id IS DISTINCT FROM OLD.case_id
             OR NEW.input_snapshot_id IS DISTINCT FROM OLD.input_snapshot_id
             OR NEW.started_by IS DISTINCT FROM OLD.started_by
             OR NEW.request_key IS DISTINCT FROM OLD.request_key THEN
            RAISE EXCEPTION 'run_binding_immutable' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER audit_run_binding_immutable BEFORE UPDATE ON audit_runs "
        "FOR EACH ROW EXECUTE FUNCTION prevent_run_rebinding()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_run_binding_immutable ON audit_runs")
    op.execute("DROP FUNCTION prevent_run_rebinding()")
