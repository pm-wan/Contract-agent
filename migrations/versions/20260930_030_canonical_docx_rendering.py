"""Persist canonical PDF render identities for complete DOCX indexing.

Revision ID: 20260930_030
Revises: 20260930_029
"""

from alembic import op

revision = "20260930_030"
down_revision = "20260930_029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE document_versions ADD COLUMN rendered_pdf_sha256 text, "
        "ADD COLUMN rendered_pdf_size_bytes bigint, ADD COLUMN renderer_identity text"
    )
    op.execute(
        "UPDATE document_versions SET parse_status = 'queued', "
        "parser_version = 'm1-docx-render-pending', parsed_sha256 = NULL, "
        "parsed_size_bytes = NULL, warnings = '[\"docx_canonical_render_pending\"]'::jsonb "
        "WHERE media_type = "
        "'application/vnd.openxmlformats-officedocument.wordprocessingml.document' "
        "AND parse_status IN ('completed', 'needs_material')"
    )
    op.execute(
        "UPDATE document_parse_jobs AS j SET status = 'queued', attempt = 0, "
        "next_run_at = now(), lease_owner = NULL, lease_until = NULL, "
        "fencing_token = j.fencing_token + 1, last_error = NULL, updated_at = now() "
        "FROM document_versions AS d WHERE d.tenant_id = j.tenant_id "
        "AND d.id = j.document_version_id AND d.media_type = "
        "'application/vnd.openxmlformats-officedocument.wordprocessingml.document' "
        "AND d.parse_status = 'queued'"
    )
    op.execute(
        "UPDATE document_index_jobs AS j SET status = 'queued', attempt = 0, "
        "next_run_at = now(), lease_owner = NULL, lease_until = NULL, "
        "fencing_token = j.fencing_token + 1, last_error = NULL, updated_at = now() "
        "FROM document_versions AS d WHERE d.tenant_id = j.tenant_id "
        "AND d.id = j.document_version_id AND d.media_type = "
        "'application/vnd.openxmlformats-officedocument.wordprocessingml.document' "
        "AND d.parse_status = 'queued'"
    )
    op.execute(
        "ALTER TABLE document_versions ADD CONSTRAINT document_render_identity_check CHECK "
        "((media_type = 'application/pdf' AND rendered_pdf_sha256 IS NULL "
        "AND rendered_pdf_size_bytes IS NULL AND renderer_identity IS NULL) OR "
        "(media_type = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' "
        "AND ((parse_status IN ('queued', 'processing', 'failed') "
        "AND rendered_pdf_sha256 IS NULL AND rendered_pdf_size_bytes IS NULL "
        "AND renderer_identity IS NULL) OR (parse_status IN ('completed', 'needs_material') "
        "AND rendered_pdf_sha256 ~ '^[a-f0-9]{64}$' "
        "AND rendered_pdf_size_bytes BETWEEN 100 AND 125829120 "
        "AND renderer_identity IS NOT NULL AND length(renderer_identity) BETWEEN 1 AND 128))))"
    )
    op.execute(
        "GRANT UPDATE (rendered_pdf_sha256, rendered_pdf_size_bytes, renderer_identity) "
        "ON document_versions TO contract_runtime"
    )
    op.execute(
        """
        CREATE FUNCTION protect_document_render_identity() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.rendered_pdf_sha256 IS NOT NULL AND
             (NEW.rendered_pdf_sha256, NEW.rendered_pdf_size_bytes, NEW.renderer_identity)
             IS DISTINCT FROM
             (OLD.rendered_pdf_sha256, OLD.rendered_pdf_size_bytes, OLD.renderer_identity) THEN
            RAISE EXCEPTION 'document_render_identity_immutable';
          END IF;
          RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER document_render_identity_immutable BEFORE UPDATE "
        "ON document_versions FOR EACH ROW EXECUTE FUNCTION protect_document_render_identity()"
    )
    op.execute(
        "ALTER TABLE document_indexes ADD COLUMN rendered_pdf_sha256 text, "
        "ADD COLUMN renderer_identity text"
    )
    op.execute(
        "ALTER TABLE document_indexes ADD CONSTRAINT document_indexes_render_identity_check "
        "CHECK ((rendered_pdf_sha256 IS NULL AND renderer_identity IS NULL) OR "
        "(rendered_pdf_sha256 ~ '^[a-f0-9]{64}$' AND renderer_identity IS NOT NULL "
        "AND length(renderer_identity) BETWEEN 1 AND 128))"
    )
    op.execute(
        "GRANT UPDATE (pageindex_doc_id, pageindex_configuration, "
        "pageindex_configuration_sha256, rendered_pdf_sha256, renderer_identity) "
        "ON document_indexes TO contract_runtime"
    )
    op.execute(
        "ALTER TABLE input_snapshot_documents ADD COLUMN rendered_pdf_sha256 text, "
        "ADD COLUMN renderer_identity text"
    )
    op.execute(
        "ALTER TABLE input_snapshot_documents ADD CONSTRAINT "
        "input_snapshot_documents_render_identity_check CHECK "
        "((rendered_pdf_sha256 IS NULL AND renderer_identity IS NULL) OR "
        "(rendered_pdf_sha256 ~ '^[a-f0-9]{64}$' AND renderer_identity IS NOT NULL "
        "AND length(renderer_identity) BETWEEN 1 AND 128))"
    )
    op.execute(
        "GRANT INSERT (rendered_pdf_sha256, renderer_identity) "
        "ON input_snapshot_documents TO contract_runtime"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE INSERT (rendered_pdf_sha256, renderer_identity) "
        "ON input_snapshot_documents FROM contract_runtime"
    )
    op.execute(
        "ALTER TABLE input_snapshot_documents DROP CONSTRAINT "
        "input_snapshot_documents_render_identity_check"
    )
    op.execute(
        "ALTER TABLE input_snapshot_documents DROP COLUMN renderer_identity, "
        "DROP COLUMN rendered_pdf_sha256"
    )
    op.execute(
        "REVOKE UPDATE (rendered_pdf_sha256, rendered_pdf_size_bytes, renderer_identity) "
        "ON document_versions FROM contract_runtime"
    )
    op.execute("DROP TRIGGER document_render_identity_immutable ON document_versions")
    op.execute("DROP FUNCTION protect_document_render_identity()")
    op.execute(
        "REVOKE UPDATE (pageindex_doc_id, pageindex_configuration, "
        "pageindex_configuration_sha256, rendered_pdf_sha256, renderer_identity) "
        "ON document_indexes FROM contract_runtime"
    )
    op.execute(
        "ALTER TABLE document_indexes DROP CONSTRAINT document_indexes_render_identity_check"
    )
    op.execute(
        "ALTER TABLE document_indexes DROP COLUMN renderer_identity, "
        "DROP COLUMN rendered_pdf_sha256"
    )
    op.execute("ALTER TABLE document_versions DROP CONSTRAINT document_render_identity_check")
    op.execute(
        "ALTER TABLE document_versions DROP COLUMN renderer_identity, "
        "DROP COLUMN rendered_pdf_size_bytes, DROP COLUMN rendered_pdf_sha256"
    )
