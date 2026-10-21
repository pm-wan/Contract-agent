"""Record immutable downstream effects of an entity correction.

Revision ID: 20260930_028
Revises: 20260930_027
"""

from alembic import op

revision = "20260930_028"
down_revision = "20260930_027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE case_legal_entity_correction_effects (
          tenant_id uuid NOT NULL,
          correction_id uuid NOT NULL,
          case_id uuid NOT NULL,
          revoked_selection_ids uuid[] NOT NULL DEFAULT '{}',
          revoked_document_version_ids uuid[] NOT NULL DEFAULT '{}',
          superseded_run_ids uuid[] NOT NULL DEFAULT '{}',
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (tenant_id, correction_id),
          FOREIGN KEY (tenant_id, correction_id)
            REFERENCES case_legal_entity_corrections (tenant_id, id),
          FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id)
        )
        """
    )
    op.execute("ALTER TABLE case_legal_entity_correction_effects ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE case_legal_entity_correction_effects FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON case_legal_entity_correction_effects
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT ON case_legal_entity_correction_effects TO contract_runtime")


def downgrade() -> None:
    op.execute("DROP TABLE case_legal_entity_correction_effects")
