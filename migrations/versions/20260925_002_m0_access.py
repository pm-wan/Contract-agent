"""M0 read-side authorization fixture for current and selected contracts.

Revision ID: 20260925_002
Revises: 20260925_001
"""

from alembic import op

revision = "20260925_002"
down_revision = "20260925_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE audit_cases ADD COLUMN legal_entity_id uuid")
    op.execute(
        """
        CREATE TABLE document_versions (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          legal_entity_id uuid NOT NULL,
          source_kind text NOT NULL CHECK (source_kind IN ('main', 'history', 'customer')),
          source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[a-f0-9]{64}$'),
          has_restricted_amount boolean NOT NULL DEFAULT true,
          status text NOT NULL DEFAULT 'active'
            CHECK (status IN ('active', 'superseded', 'revoked')),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, id),
          UNIQUE (tenant_id, id, source_sha256)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE case_members (
          tenant_id uuid NOT NULL,
          case_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          role text NOT NULL,
          granted_at timestamptz NOT NULL DEFAULT now(),
          revoked_at timestamptz,
          PRIMARY KEY (tenant_id, case_id, actor_id),
          FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE contract_selections (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          case_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          relation text NOT NULL CHECK (relation IN ('main', 'background', 'upstream')),
          selected_by uuid NOT NULL,
          selected_at timestamptz NOT NULL DEFAULT now(),
          revoked_at timestamptz,
          PRIMARY KEY (tenant_id, id),
          FOREIGN KEY (tenant_id, case_id, selected_by)
            REFERENCES case_members (tenant_id, case_id, actor_id),
          FOREIGN KEY (tenant_id, document_version_id)
            REFERENCES document_versions (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX case_active_selection_idx
          ON contract_selections (tenant_id, case_id, document_version_id, relation)
          WHERE revoked_at IS NULL
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX case_one_active_main_idx
          ON contract_selections (tenant_id, case_id)
          WHERE relation = 'main' AND revoked_at IS NULL
        """
    )
    op.execute(
        """
        CREATE TABLE document_grants (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          actor_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          purpose text NOT NULL CHECK (purpose IN ('contract_review', 'upstream_comparison')),
          allow_text boolean NOT NULL DEFAULT false,
          allow_amount boolean NOT NULL DEFAULT false,
          valid_until timestamptz NOT NULL,
          revoked_at timestamptz,
          granted_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, id),
          FOREIGN KEY (tenant_id, document_version_id)
            REFERENCES document_versions (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX active_document_grant_idx
          ON document_grants (tenant_id, actor_id, document_version_id, purpose)
          WHERE revoked_at IS NULL
        """
    )
    op.execute(
        """
        CREATE TABLE document_indexes (
          tenant_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          source_sha256 text NOT NULL,
          pageindex_doc_id text NOT NULL CHECK (length(pageindex_doc_id) > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, document_version_id),
          UNIQUE (tenant_id, pageindex_doc_id),
          FOREIGN KEY (tenant_id, document_version_id, source_sha256)
            REFERENCES document_versions (tenant_id, id, source_sha256)
        )
        """
    )
    for table in (
        "document_versions",
        "case_members",
        "contract_selections",
        "document_grants",
        "document_indexes",
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
              USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
              WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            """
        )
        op.execute(f"GRANT SELECT ON {table} TO contract_runtime")


def downgrade() -> None:
    for table in (
        "document_indexes",
        "document_grants",
        "contract_selections",
        "case_members",
        "document_versions",
    ):
        op.execute(f"DROP TABLE {table}")
    op.execute("ALTER TABLE audit_cases DROP COLUMN legal_entity_id")
