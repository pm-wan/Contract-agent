"""Versioned source documents for contract-driven AI research."""

from alembic import op

revision = "20261020_038"
down_revision = "20261019_037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE knowledge_documents (
          tenant_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(),
          source_key uuid NOT NULL, version integer NOT NULL CHECK (version > 0),
          kind text NOT NULL CHECK (kind IN ('legal', 'company')),
          title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 255),
          file_name text NOT NULL, media_type text NOT NULL,
          source_url text NOT NULL DEFAULT '', issuer text NOT NULL DEFAULT '',
          scope text NOT NULL DEFAULT '', effective_from date, effective_until date,
          status text NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','published','withdrawn')),
          content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[a-f0-9]{64}$'),
          file_data bytea NOT NULL, segments jsonb NOT NULL,
          warnings jsonb NOT NULL DEFAULT '[]',
          created_by uuid NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
          published_at timestamptz, withdrawn_at timestamptz,
          PRIMARY KEY (tenant_id,id), UNIQUE (tenant_id,source_key,version),
          FOREIGN KEY (tenant_id,created_by) REFERENCES app_users(tenant_id,actor_id),
          CHECK (effective_until IS NULL OR effective_from IS NULL OR effective_until >= effective_from),
          CHECK (jsonb_typeof(segments) = 'array' AND jsonb_array_length(segments) > 0)
        )
    """)
    op.execute("ALTER TABLE knowledge_documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_documents FORCE ROW LEVEL SECURITY")
    op.execute("""CREATE POLICY tenant_isolation ON knowledge_documents
        USING (tenant_id = nullif(current_setting('app.tenant_id', true),'')::uuid)
        WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true),'')::uuid)""")
    op.execute("GRANT SELECT, INSERT ON knowledge_documents TO contract_runtime")
    op.execute("GRANT UPDATE (status,published_at,withdrawn_at) ON knowledge_documents TO contract_runtime")
    op.execute("CREATE INDEX knowledge_documents_scope ON knowledge_documents(tenant_id,kind,status)")
    op.execute("""CREATE FUNCTION guard_knowledge_document() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF (NEW.status = 'published' AND OLD.status = 'draft') OR
           (NEW.status = 'withdrawn' AND OLD.status = 'published') THEN RETURN NEW; END IF;
        RAISE EXCEPTION 'knowledge_document_transition_invalid' USING ERRCODE = '23514';
      END; $$""")
    op.execute("""CREATE TRIGGER knowledge_document_guard BEFORE UPDATE ON knowledge_documents
      FOR EACH ROW EXECUTE FUNCTION guard_knowledge_document()""")


def downgrade() -> None:
    op.execute("DROP TABLE knowledge_documents")
    op.execute("DROP FUNCTION guard_knowledge_document()")
