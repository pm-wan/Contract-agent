"""Add separately provisioned tenant entity administrators and immutable corrections.

Revision ID: 20260930_027
Revises: 20260930_026
"""

from alembic import op

revision = "20260930_027"
down_revision = "20260930_026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE tenant_entity_administrators (
          tenant_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          granted_by uuid NOT NULL,
          granted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          revoked_by uuid,
          revoked_at timestamptz,
          PRIMARY KEY (tenant_id, actor_id),
          CHECK ((revoked_at IS NULL) = (revoked_by IS NULL))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE case_legal_entity_corrections (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          case_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          previous_legal_entity_id uuid NOT NULL,
          legal_entity_id uuid NOT NULL,
          expected_case_version bigint NOT NULL CHECK (expected_case_version > 0),
          case_version bigint NOT NULL CHECK (case_version > expected_case_version),
          correction_note text NOT NULL CHECK (length(btrim(correction_note)) BETWEEN 1 AND 500),
          idempotency_key text NOT NULL CHECK
            (idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'),
          request_sha256 text NOT NULL CHECK (request_sha256 ~ '^[a-f0-9]{64}$'),
          corrected_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (tenant_id, id),
          UNIQUE (tenant_id, case_id, idempotency_key),
          FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
          CHECK (previous_legal_entity_id <> legal_entity_id)
        )
        """
    )
    for table in ("tenant_entity_administrators", "case_legal_entity_corrections"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
              USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
              WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            """
        )
    # Administrator assignments are provisioned/revoked outside the application API.
    op.execute("GRANT SELECT ON tenant_entity_administrators TO contract_runtime")
    # The runtime can append and inspect correction evidence, but cannot rewrite or erase it.
    op.execute("GRANT SELECT, INSERT ON case_legal_entity_corrections TO contract_runtime")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT ON case_legal_entity_corrections FROM contract_runtime")
    op.execute("REVOKE SELECT ON tenant_entity_administrators FROM contract_runtime")
    op.execute("DROP TABLE case_legal_entity_corrections")
    op.execute("DROP TABLE tenant_entity_administrators")
