"""Persist immutable, input-bound human-confirmed fact set snapshots.

Revision ID: 20260930_032
Revises: 20260930_031
"""

from alembic import op

revision = "20260930_032"
down_revision = "20260930_031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE audit_runs ADD CONSTRAINT audit_run_case_identity_fact "
        "UNIQUE (tenant_id, case_id, id)"
    )
    op.execute(
        """
        CREATE TABLE fact_set_snapshots (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          case_id uuid NOT NULL,
          input_snapshot_id uuid NOT NULL,
          source_run_id uuid NOT NULL,
          version bigint NOT NULL CHECK (version > 0),
          facts jsonb NOT NULL CHECK (jsonb_typeof(facts) = 'object'),
          content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
          created_by uuid NOT NULL,
          reason text NOT NULL CHECK (length(reason) BETWEEN 1 AND 4000),
          idempotency_key text NOT NULL CHECK (length(idempotency_key) BETWEEN 8 AND 128),
          request_hash text NOT NULL CHECK (request_hash ~ '^[a-f0-9]{64}$'),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (tenant_id, id),
          FOREIGN KEY (tenant_id, case_id, input_snapshot_id)
            REFERENCES input_snapshots (tenant_id, case_id, id),
          FOREIGN KEY (tenant_id, case_id, source_run_id)
            REFERENCES audit_runs (tenant_id, case_id, id),
          FOREIGN KEY (tenant_id, case_id, created_by)
            REFERENCES case_members (tenant_id, case_id, actor_id),
          UNIQUE (tenant_id, case_id, input_snapshot_id, version),
          UNIQUE (tenant_id, idempotency_key)
        )
        """
    )
    op.execute("ALTER TABLE fact_set_snapshots ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE fact_set_snapshots FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON fact_set_snapshots
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute(
        "CREATE INDEX fact_set_latest_idx ON fact_set_snapshots "
        "(tenant_id, case_id, input_snapshot_id, version DESC)"
    )
    op.execute("GRANT SELECT, INSERT ON fact_set_snapshots TO contract_runtime")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT ON fact_set_snapshots FROM contract_runtime")
    op.execute("DROP TABLE fact_set_snapshots")
    op.execute("ALTER TABLE audit_runs DROP CONSTRAINT audit_run_case_identity_fact")
