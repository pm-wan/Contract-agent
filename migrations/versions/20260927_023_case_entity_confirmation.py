"""Allow staged primary uploads before reviewer-confirmed case entity scope.

Revision ID: 20260927_023
Revises: 20260927_022
"""

from alembic import op

revision = "20260927_023"
down_revision = "20260927_022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE audit_cases ADD COLUMN create_key text")
    op.execute("ALTER TABLE audit_cases ADD COLUMN create_request_sha256 text")
    op.execute(
        "ALTER TABLE audit_cases ADD CONSTRAINT audit_cases_create_idempotency_check CHECK "
        "((create_key IS NULL AND create_request_sha256 IS NULL) OR "
        "(create_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$' "
        "AND create_request_sha256 ~ '^[a-f0-9]{64}$'))"
    )
    op.execute(
        "CREATE UNIQUE INDEX audit_cases_create_key_idx "
        "ON audit_cases (tenant_id, create_key) WHERE create_key IS NOT NULL"
    )
    op.execute("ALTER TABLE document_versions ALTER COLUMN legal_entity_id DROP NOT NULL")
    op.execute(
        """
        CREATE TABLE case_legal_entity_confirmations (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          case_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          legal_entity_id uuid NOT NULL,
          expected_case_version bigint NOT NULL CHECK (expected_case_version > 0),
          case_version bigint NOT NULL CHECK (case_version > 0),
          verification_note text NOT NULL CHECK
            (length(btrim(verification_note)) BETWEEN 1 AND 500),
          idempotency_key text NOT NULL CHECK
            (idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'),
          request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[a-f0-9]{64}$'),
          confirmed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (tenant_id, id),
          UNIQUE (tenant_id, case_id, idempotency_key),
          FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
          FOREIGN KEY (tenant_id, case_id, actor_id)
            REFERENCES case_members (tenant_id, case_id, actor_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX case_legal_entity_confirmations_latest_idx "
        "ON case_legal_entity_confirmations (tenant_id, case_id, confirmed_at DESC, id DESC)"
    )
    op.execute("ALTER TABLE case_legal_entity_confirmations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE case_legal_entity_confirmations FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON case_legal_entity_confirmations
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT ON case_legal_entity_confirmations TO contract_runtime")
    op.execute("GRANT UPDATE (legal_entity_id, version) ON audit_cases TO contract_runtime")
    op.execute("GRANT UPDATE (legal_entity_id) ON document_versions TO contract_runtime")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (legal_entity_id) ON document_versions FROM contract_runtime")
    op.execute("REVOKE UPDATE (legal_entity_id, version) ON audit_cases FROM contract_runtime")
    op.execute("REVOKE SELECT, INSERT ON case_legal_entity_confirmations FROM contract_runtime")
    op.execute("DROP TABLE case_legal_entity_confirmations")
    op.execute("DROP INDEX audit_cases_create_key_idx")
    op.execute("ALTER TABLE audit_cases DROP CONSTRAINT audit_cases_create_idempotency_check")
    op.execute("ALTER TABLE audit_cases DROP COLUMN create_request_sha256")
    op.execute("ALTER TABLE audit_cases DROP COLUMN create_key")
    op.execute("ALTER TABLE document_versions ALTER COLUMN legal_entity_id SET NOT NULL")
