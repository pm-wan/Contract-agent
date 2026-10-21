"""Persist actor-bound source previews and immutable document input snapshots.

Revision ID: 20260925_008
Revises: 20260925_007
"""

from alembic import op

revision = "20260925_008"
down_revision = "20260925_007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE source_previews (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          case_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          purpose text NOT NULL CHECK
            (purpose IN ('contract_review', 'upstream_comparison')),
          require_amount boolean NOT NULL,
          case_version bigint NOT NULL CHECK (case_version > 0),
          manifest jsonb NOT NULL,
          manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[a-f0-9]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          expires_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, id),
          FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
          FOREIGN KEY (tenant_id, case_id, actor_id)
            REFERENCES case_members (tenant_id, case_id, actor_id),
          CHECK (expires_at > created_at)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE input_snapshots (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          case_id uuid NOT NULL,
          preview_id uuid NOT NULL,
          frozen_by uuid NOT NULL,
          purpose text NOT NULL CHECK
            (purpose IN ('contract_review', 'upstream_comparison')),
          require_amount boolean NOT NULL,
          case_version bigint NOT NULL CHECK (case_version > 0),
          manifest jsonb NOT NULL,
          manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[a-f0-9]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, id),
          UNIQUE (tenant_id, preview_id),
          FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
          FOREIGN KEY (tenant_id, preview_id)
            REFERENCES source_previews (tenant_id, id),
          FOREIGN KEY (tenant_id, case_id, frozen_by)
            REFERENCES case_members (tenant_id, case_id, actor_id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE input_snapshot_documents (
          tenant_id uuid NOT NULL,
          snapshot_id uuid NOT NULL,
          document_version_id uuid NOT NULL,
          selection_id uuid NOT NULL,
          source_sha256 text NOT NULL CHECK (source_sha256 ~ '^[a-f0-9]{64}$'),
          relation text NOT NULL CHECK (relation IN ('main', 'background', 'upstream')),
          PRIMARY KEY (tenant_id, snapshot_id, document_version_id),
          FOREIGN KEY (tenant_id, snapshot_id)
            REFERENCES input_snapshots (tenant_id, id),
          FOREIGN KEY (tenant_id, document_version_id, source_sha256)
            REFERENCES document_versions (tenant_id, id, source_sha256),
          FOREIGN KEY (tenant_id, selection_id)
            REFERENCES contract_selections (tenant_id, id)
        )
        """
    )
    for table in ("source_previews", "input_snapshots", "input_snapshot_documents"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
              USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
              WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
            """
        )
        op.execute(f"GRANT SELECT, INSERT ON {table} TO contract_runtime")
    op.execute(
        "CREATE INDEX input_snapshot_doc_lookup_idx ON input_snapshot_documents "
        "(tenant_id, snapshot_id, document_version_id, source_sha256)"
    )


def downgrade() -> None:
    for table in ("input_snapshot_documents", "input_snapshots", "source_previews"):
        op.execute(f"DROP TABLE {table}")
