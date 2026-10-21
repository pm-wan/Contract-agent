"""Bind exact development rule and Skill sources to each review run.

Revision ID: 20260928_024
Revises: 20260927_023
"""

from alembic import op

revision = "20260928_024"
down_revision = "20260927_023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE run_definition_snapshots (
          tenant_id uuid NOT NULL,
          run_id uuid NOT NULL,
          snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (tenant_id, run_id),
          FOREIGN KEY (tenant_id, run_id) REFERENCES audit_runs (tenant_id, id)
        )
        """
    )
    op.execute("ALTER TABLE run_definition_snapshots ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE run_definition_snapshots FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON run_definition_snapshots
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT ON run_definition_snapshots TO contract_runtime")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT ON run_definition_snapshots FROM contract_runtime")
    op.execute("DROP TABLE run_definition_snapshots")
