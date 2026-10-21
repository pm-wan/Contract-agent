"""Queue a PageIndex build for each immutable uploaded document version.

Revision ID: 20260926_018
Revises: 20260926_017
"""

from alembic import op

revision = "20260926_018"
down_revision = "20260926_017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE document_index_jobs (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          case_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[a-f0-9]{64}$'),
          status text NOT NULL DEFAULT 'queued'
            CHECK (status IN ('queued', 'running', 'completed', 'failed')),
          attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
          next_run_at timestamptz NOT NULL DEFAULT now(),
          lease_owner text,
          lease_until timestamptz,
          fencing_token bigint NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
          last_error text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, id),
          UNIQUE (tenant_id, document_version_id),
          FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
          FOREIGN KEY (tenant_id, document_version_id, source_sha256)
            REFERENCES document_versions (tenant_id, id, source_sha256),
          CHECK (status <> 'running' OR (lease_owner IS NOT NULL AND lease_until IS NOT NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX document_index_jobs_claim_idx "
        "ON document_index_jobs (next_run_at, created_at) WHERE status IN ('queued', 'running')"
    )
    op.execute("ALTER TABLE document_index_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_index_jobs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON document_index_jobs
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT ON document_index_jobs TO contract_runtime")
    op.execute(
        "GRANT UPDATE (status, attempt, next_run_at, lease_owner, lease_until, "
        "fencing_token, last_error, updated_at) ON document_index_jobs TO contract_runtime"
    )
    op.execute("GRANT INSERT ON document_indexes TO contract_runtime")


def downgrade() -> None:
    op.execute("REVOKE INSERT ON document_indexes FROM contract_runtime")
    op.execute(
        "REVOKE UPDATE (status, attempt, next_run_at, lease_owner, lease_until, "
        "fencing_token, last_error, updated_at) ON document_index_jobs FROM contract_runtime"
    )
    op.execute("REVOKE SELECT, INSERT ON document_index_jobs FROM contract_runtime")
    op.execute("DROP TABLE document_index_jobs")
