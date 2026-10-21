"""Allow explicit selection to activate a previously staged upload.

Revision ID: 20260926_013
Revises: 20260926_012
"""

from alembic import op

revision = "20260926_013"
down_revision = "20260926_012"
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
         AND size_bytes > 0 AND parse_status IN ('completed', 'needs_material')
         AND parser_version IS NOT NULL
         AND parsed_sha256 ~ '^[a-f0-9]{64}$' AND parsed_size_bytes > 0
         AND warnings IS NOT NULL AND jsonb_typeof(warnings) = 'array'
         AND upload_case_version > 0 AND status IN ('staged', 'active')))
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM document_versions
            WHERE upload_key IS NOT NULL AND status = 'active'
          ) THEN
            RAISE EXCEPTION 'cannot_downgrade_with_active_uploads';
          END IF;
        END $$
        """
    )
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
         AND parser_version IS NOT NULL
         AND parsed_sha256 ~ '^[a-f0-9]{64}$' AND parsed_size_bytes > 0
         AND warnings IS NOT NULL AND jsonb_typeof(warnings) = 'array'
         AND upload_case_version > 0 AND status = 'staged'))
        """
    )
