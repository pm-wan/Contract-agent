"""Audit reviewer selection grants and support least-privilege revocation.

Revision ID: 20260926_015
Revises: 20260926_014
"""

from alembic import op

revision = "20260926_015"
down_revision = "20260926_014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE document_access_events (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          case_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          selection_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          action text NOT NULL CHECK (action IN ('selection_granted', 'selection_revoked')),
          permissions jsonb NOT NULL CHECK (jsonb_typeof(permissions) = 'array'),
          reason text,
          idempotency_key text,
          case_version bigint NOT NULL CHECK (case_version > 0),
          invalidated_run_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, id),
          FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
          FOREIGN KEY (tenant_id, document_version_id)
            REFERENCES document_versions (tenant_id, id),
          FOREIGN KEY (tenant_id, selection_id) REFERENCES contract_selections (tenant_id, id),
          FOREIGN KEY (tenant_id, case_id, actor_id)
            REFERENCES case_members (tenant_id, case_id, actor_id),
          CHECK ((action = 'selection_granted' AND reason IS NULL AND idempotency_key IS NULL)
              OR (action = 'selection_revoked' AND reason IS NOT NULL
                  AND length(trim(reason)) BETWEEN 1 AND 500
                  AND idempotency_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$'))
        )
        """
    )
    op.execute("ALTER TABLE document_access_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_access_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON document_access_events
        USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX document_access_event_idempotency_idx "
        "ON document_access_events (tenant_id, idempotency_key) WHERE idempotency_key IS NOT NULL"
    )
    op.execute("GRANT SELECT, INSERT ON document_access_events TO contract_runtime")
    op.execute("GRANT UPDATE (revoked_at) ON contract_selections TO contract_runtime")
    op.execute("GRANT UPDATE (revoked_at) ON document_grants TO contract_runtime")


def downgrade() -> None:
    op.execute("REVOKE UPDATE (revoked_at) ON document_grants FROM contract_runtime")
    op.execute("REVOKE UPDATE (revoked_at) ON contract_selections FROM contract_runtime")
    op.execute("REVOKE SELECT, INSERT ON document_access_events FROM contract_runtime")
    op.execute("DROP TABLE document_access_events")
