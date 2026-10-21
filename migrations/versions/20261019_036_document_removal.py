"""Support audited removal of uploaded materials from a case."""

from alembic import op

revision = "20261019_036"
down_revision = "20261018_035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE document_versions ADD COLUMN removed_at timestamptz")
    op.execute("ALTER TABLE document_versions ADD COLUMN removed_by uuid")
    op.execute(
        "ALTER TABLE document_versions ADD CONSTRAINT document_removal_fields_check "
        "CHECK ((removed_at IS NULL) = (removed_by IS NULL))"
    )
    op.execute(
        "ALTER TABLE document_versions ADD CONSTRAINT document_removed_by_fk "
        "FOREIGN KEY (tenant_id, uploaded_case_id, removed_by) "
        "REFERENCES case_members (tenant_id, case_id, actor_id)"
    )
    op.execute("GRANT UPDATE (removed_at, removed_by) ON document_versions TO contract_runtime")

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
         AND upload_case_version > 0 AND status IN ('staged', 'active', 'superseded', 'revoked')
         AND ((removed_at IS NULL AND removed_by IS NULL)
           OR (status = 'revoked' AND removed_at IS NOT NULL AND removed_by IS NOT NULL))))
        """
    )
    op.execute("DROP TRIGGER document_status_transition_guard ON document_versions")
    op.execute("DROP FUNCTION guard_document_status_transition()")
    op.execute(
        """
        CREATE FUNCTION guard_document_status_transition() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.status = OLD.status
             OR (OLD.status = 'staged' AND NEW.status = 'active')
             OR (OLD.status = 'active' AND NEW.status IN ('superseded', 'revoked'))
             OR (OLD.status = 'superseded' AND NEW.status = 'revoked')
             OR (OLD.status = 'staged' AND NEW.status = 'revoked'
                 AND NEW.removed_at IS NOT NULL AND NEW.removed_by IS NOT NULL) THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'document_status_transition_invalid' USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER document_status_transition_guard BEFORE UPDATE OF status "
        "ON document_versions FOR EACH ROW EXECUTE FUNCTION guard_document_status_transition()"
    )

    op.execute(
        """
        CREATE TABLE document_removal_events (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          case_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          reason text NOT NULL CHECK (length(btrim(reason)) BETWEEN 1 AND 500),
          idempotency_key text NOT NULL
            CHECK (idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'),
          expected_case_version bigint NOT NULL CHECK (expected_case_version > 0),
          case_version bigint NOT NULL CHECK (case_version > 0),
          revoked_selection_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
          revoked_grant_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
          invalidated_run_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
          permissions jsonb NOT NULL DEFAULT '[]'::jsonb
            CHECK (jsonb_typeof(permissions) = 'array'),
          removed_at timestamptz NOT NULL,
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (tenant_id, id),
          UNIQUE (tenant_id, idempotency_key),
          FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
          FOREIGN KEY (tenant_id, document_version_id)
            REFERENCES document_versions (tenant_id, id),
          FOREIGN KEY (tenant_id, case_id, actor_id)
            REFERENCES case_members (tenant_id, case_id, actor_id)
        )
        """
    )
    op.execute("ALTER TABLE document_removal_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_removal_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON document_removal_events
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute(
        "CREATE INDEX document_removal_events_case_idx "
        "ON document_removal_events (tenant_id, case_id, created_at DESC)"
    )
    op.execute("GRANT SELECT, INSERT ON document_removal_events TO contract_runtime")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT ON document_removal_events FROM contract_runtime")
    op.execute("DROP TABLE document_removal_events")
    op.execute("DROP TRIGGER document_status_transition_guard ON document_versions")
    op.execute("DROP FUNCTION guard_document_status_transition()")
    op.execute(
        """
        CREATE FUNCTION guard_document_status_transition() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.status = OLD.status
             OR (OLD.status = 'staged' AND NEW.status = 'active')
             OR (OLD.status = 'active' AND NEW.status IN ('superseded', 'revoked')) THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'document_status_transition_invalid' USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER document_status_transition_guard BEFORE UPDATE OF status "
        "ON document_versions FOR EACH ROW EXECUTE FUNCTION guard_document_status_transition()"
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
    op.execute("REVOKE UPDATE (removed_at, removed_by) ON document_versions FROM contract_runtime")
    op.execute("ALTER TABLE document_versions DROP CONSTRAINT document_removed_by_fk")
    op.execute("ALTER TABLE document_versions DROP CONSTRAINT document_removal_fields_check")
    op.execute("ALTER TABLE document_versions DROP COLUMN removed_by, DROP COLUMN removed_at")
