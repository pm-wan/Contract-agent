"""Queue document parsing as a recoverable tenant-scoped job.

Revision ID: 20260927_022
Revises: 20260927_021
"""

from alembic import op

revision = "20260927_022"
down_revision = "20260927_021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE document_versions DROP CONSTRAINT document_upload_metadata_check")
    op.execute(
        """
        ALTER TABLE document_versions ADD CONSTRAINT document_upload_metadata_check CHECK
        (upload_key IS NULL OR (uploaded_by IS NOT NULL
         AND storage_backend IN ('local', 's3')
         AND source_name IS NOT NULL AND length(source_name) BETWEEN 1 AND 255
         AND media_type IN ('application/pdf',
           'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
         AND size_bytes > 0
         AND parse_status IN ('queued', 'processing', 'completed', 'needs_material', 'failed')
         AND parser_version IS NOT NULL
         AND ((parse_status IN ('completed', 'needs_material')
               AND parsed_sha256 ~ '^[a-f0-9]{64}$' AND parsed_size_bytes > 0)
           OR (parse_status IN ('queued', 'processing', 'failed')
               AND parsed_sha256 IS NULL AND parsed_size_bytes IS NULL))
         AND warnings IS NOT NULL AND jsonb_typeof(warnings) = 'array'
         AND upload_case_version > 0 AND status IN ('staged', 'active')))
        """
    )
    op.execute(
        """
        CREATE TABLE document_parse_jobs (
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
        "CREATE INDEX document_parse_jobs_claim_idx "
        "ON document_parse_jobs (next_run_at, created_at) WHERE status IN ('queued', 'running')"
    )
    op.execute("ALTER TABLE document_parse_jobs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_parse_jobs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON document_parse_jobs
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT ON document_parse_jobs TO contract_runtime")
    op.execute(
        "GRANT UPDATE (status, attempt, next_run_at, lease_owner, lease_until, "
        "fencing_token, last_error, updated_at) ON document_parse_jobs TO contract_runtime"
    )
    op.execute(
        "GRANT UPDATE (parse_status, parser_version, parsed_sha256, parsed_size_bytes, warnings) "
        "ON document_versions TO contract_runtime"
    )
    op.execute("GRANT INSERT ON document_index_jobs TO contract_runtime")


def downgrade() -> None:
    op.execute("REVOKE INSERT ON document_index_jobs FROM contract_runtime")
    op.execute(
        "REVOKE UPDATE (parse_status, parser_version, parsed_sha256, parsed_size_bytes, warnings) "
        "ON document_versions FROM contract_runtime"
    )
    op.execute(
        "REVOKE UPDATE (status, attempt, next_run_at, lease_owner, lease_until, "
        "fencing_token, last_error, updated_at) ON document_parse_jobs FROM contract_runtime"
    )
    op.execute("REVOKE SELECT, INSERT ON document_parse_jobs FROM contract_runtime")
    op.execute("DROP TABLE document_parse_jobs")
    op.execute("ALTER TABLE document_versions DROP CONSTRAINT document_upload_metadata_check")
    op.execute(
        """
        ALTER TABLE document_versions ADD CONSTRAINT document_upload_metadata_check CHECK
        (upload_key IS NULL OR (uploaded_by IS NOT NULL
         AND storage_backend IN ('local', 's3')
         AND source_name IS NOT NULL AND length(source_name) BETWEEN 1 AND 255
         AND media_type IN ('application/pdf',
           'application/vnd.openxmlformats-officedocument.wordprocessingml.document')
         AND size_bytes > 0 AND parse_status IN ('completed', 'needs_material')
         AND parser_version IS NOT NULL AND parsed_sha256 ~ '^[a-f0-9]{64}$'
         AND parsed_size_bytes > 0 AND warnings IS NOT NULL
         AND jsonb_typeof(warnings) = 'array' AND upload_case_version > 0
         AND status IN ('staged', 'active')))
        """
    )
