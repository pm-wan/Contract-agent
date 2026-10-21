"""Record staged document uploads and immutable parse artifact references.

Revision ID: 20260926_011
Revises: 20260925_010
"""

from alembic import op

revision = "20260926_011"
down_revision = "20260925_010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE document_versions DROP CONSTRAINT document_versions_source_kind_check")
    op.execute(
        "ALTER TABLE document_versions ADD CONSTRAINT document_versions_source_kind_check "
        "CHECK (source_kind IN ('main', 'history', 'customer', 'attachment'))"
    )
    op.execute("ALTER TABLE document_versions DROP CONSTRAINT document_versions_status_check")
    op.execute(
        "ALTER TABLE document_versions ADD CONSTRAINT document_versions_status_check "
        "CHECK (status IN ('staged', 'active', 'superseded', 'revoked'))"
    )
    op.execute("ALTER TABLE document_versions ADD COLUMN uploaded_case_id uuid")
    op.execute("ALTER TABLE document_versions ADD COLUMN upload_key text")
    op.execute("ALTER TABLE document_versions ADD COLUMN uploaded_by uuid")
    op.execute("ALTER TABLE document_versions ADD COLUMN storage_backend text")
    op.execute("ALTER TABLE document_versions ADD COLUMN source_name text")
    op.execute("ALTER TABLE document_versions ADD COLUMN media_type text")
    op.execute("ALTER TABLE document_versions ADD COLUMN size_bytes bigint")
    op.execute("ALTER TABLE document_versions ADD COLUMN parse_status text")
    op.execute("ALTER TABLE document_versions ADD COLUMN parser_version text")
    op.execute("ALTER TABLE document_versions ADD COLUMN parsed_sha256 text")
    op.execute("ALTER TABLE document_versions ADD COLUMN parsed_size_bytes bigint")
    op.execute("ALTER TABLE document_versions ADD COLUMN warnings jsonb")
    op.execute("ALTER TABLE document_versions ADD COLUMN upload_case_version bigint")
    op.execute(
        "CREATE UNIQUE INDEX document_upload_idempotency_idx "
        "ON document_versions (tenant_id, uploaded_case_id, upload_key) "
        "WHERE upload_key IS NOT NULL"
    )
    op.execute(
        "ALTER TABLE document_versions ADD CONSTRAINT document_upload_metadata_check CHECK "
        "(upload_key IS NULL OR (uploaded_by IS NOT NULL "
        "AND storage_backend IN ('local', 's3') "
        "AND source_name IS NOT NULL AND length(source_name) BETWEEN 1 AND 255 "
        "AND media_type IN ('application/pdf', "
        "'application/vnd.openxmlformats-officedocument.wordprocessingml.document') "
        "AND size_bytes > 0 AND parse_status IN ('completed', 'needs_material') "
        "AND parser_version IS NOT NULL "
        "AND parsed_sha256 ~ '^[a-f0-9]{64}$' AND parsed_size_bytes > 0 "
        "AND warnings IS NOT NULL AND jsonb_typeof(warnings) = 'array' "
        "AND upload_case_version > 0 AND status = 'staged'))"
    )
    op.execute(
        "ALTER TABLE document_versions ADD CONSTRAINT document_upload_case_fk "
        "FOREIGN KEY (tenant_id, uploaded_case_id) REFERENCES audit_cases (tenant_id, id)"
    )
    op.execute(
        "ALTER TABLE document_versions ADD CONSTRAINT document_upload_actor_fk "
        "FOREIGN KEY (tenant_id, uploaded_case_id, uploaded_by) "
        "REFERENCES case_members (tenant_id, case_id, actor_id)"
    )
    op.execute("GRANT INSERT ON document_versions TO contract_runtime")


def downgrade() -> None:
    op.execute("REVOKE INSERT ON document_versions FROM contract_runtime")
    op.execute("ALTER TABLE document_versions DROP CONSTRAINT document_upload_actor_fk")
    op.execute("ALTER TABLE document_versions DROP CONSTRAINT document_upload_case_fk")
    op.execute("ALTER TABLE document_versions DROP CONSTRAINT document_upload_metadata_check")
    op.execute("DROP INDEX document_upload_idempotency_idx")
    op.execute("ALTER TABLE document_versions DROP COLUMN upload_case_version")
    op.execute("ALTER TABLE document_versions DROP COLUMN warnings")
    op.execute("ALTER TABLE document_versions DROP COLUMN parsed_size_bytes")
    op.execute("ALTER TABLE document_versions DROP COLUMN parsed_sha256")
    op.execute("ALTER TABLE document_versions DROP COLUMN parser_version")
    op.execute("ALTER TABLE document_versions DROP COLUMN parse_status")
    op.execute("ALTER TABLE document_versions DROP COLUMN size_bytes")
    op.execute("ALTER TABLE document_versions DROP COLUMN media_type")
    op.execute("ALTER TABLE document_versions DROP COLUMN source_name")
    op.execute("ALTER TABLE document_versions DROP COLUMN storage_backend")
    op.execute("ALTER TABLE document_versions DROP COLUMN uploaded_by")
    op.execute("ALTER TABLE document_versions DROP COLUMN upload_key")
    op.execute("ALTER TABLE document_versions DROP COLUMN uploaded_case_id")
    op.execute("ALTER TABLE document_versions DROP CONSTRAINT document_versions_status_check")
    op.execute(
        "ALTER TABLE document_versions ADD CONSTRAINT document_versions_status_check "
        "CHECK (status IN ('active', 'superseded', 'revoked'))"
    )
    op.execute("ALTER TABLE document_versions DROP CONSTRAINT document_versions_source_kind_check")
    op.execute(
        "ALTER TABLE document_versions ADD CONSTRAINT document_versions_source_kind_check "
        "CHECK (source_kind IN ('main', 'history', 'customer'))"
    )
