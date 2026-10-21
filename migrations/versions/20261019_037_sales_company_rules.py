"""Add versioned tenant-scoped company rules for sales contracts."""

from alembic import op

revision = "20261019_037"
down_revision = "20261019_036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE company_rule_versions (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          rule_key text NOT NULL CHECK (rule_key ~ '^[a-z][a-z0-9._-]{2,119}$'),
          version integer NOT NULL CHECK (version > 0),
          contract_type text NOT NULL DEFAULT 'sales_contract'
            CHECK (contract_type = 'sales_contract'),
          title text NOT NULL CHECK (length(btrim(title)) BETWEEN 1 AND 160),
          source_name text NOT NULL CHECK (length(btrim(source_name)) BETWEEN 1 AND 255),
          source_clause text NOT NULL CHECK (length(btrim(source_clause)) BETWEEN 1 AND 10000),
          rule_type text NOT NULL CHECK (rule_type IN (
            'discount_limit', 'credit_days_limit', 'approval_amount',
            'template_deviation_approval')),
          threshold numeric(18, 4),
          role_scope text NOT NULL DEFAULT 'seller'
            CHECK (role_scope IN ('seller', 'buyer', 'either')),
          effective_from date NOT NULL,
          effective_until date,
          status text NOT NULL DEFAULT 'draft'
            CHECK (status IN ('draft', 'review', 'published', 'withdrawn')),
          content_sha256 text CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[a-f0-9]{64}$'),
          created_by uuid NOT NULL,
          reviewed_by uuid,
          published_by uuid,
          withdrawn_by uuid,
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          published_at timestamptz,
          withdrawn_at timestamptz,
          PRIMARY KEY (tenant_id, id),
          UNIQUE (tenant_id, rule_key, version),
          FOREIGN KEY (tenant_id, created_by) REFERENCES app_users (tenant_id, actor_id),
          FOREIGN KEY (tenant_id, reviewed_by) REFERENCES app_users (tenant_id, actor_id),
          FOREIGN KEY (tenant_id, published_by) REFERENCES app_users (tenant_id, actor_id),
          FOREIGN KEY (tenant_id, withdrawn_by) REFERENCES app_users (tenant_id, actor_id),
          CHECK (effective_until IS NULL OR effective_until >= effective_from),
          CHECK ((status IN ('published', 'withdrawn') AND content_sha256 IS NOT NULL
                   AND reviewed_by IS NOT NULL AND published_by IS NOT NULL AND published_at IS NOT NULL)
              OR (status IN ('draft', 'review') AND published_at IS NULL)),
          CHECK ((status = 'withdrawn' AND withdrawn_by IS NOT NULL AND withdrawn_at IS NOT NULL)
              OR (status <> 'withdrawn' AND withdrawn_by IS NULL AND withdrawn_at IS NULL)),
          CHECK ((rule_type = 'template_deviation_approval' AND threshold IS NULL)
              OR (rule_type <> 'template_deviation_approval' AND threshold IS NOT NULL AND threshold >= 0))
        )
        """
    )
    op.execute(
        "CREATE INDEX company_rule_versions_scope_idx ON company_rule_versions "
        "(tenant_id, contract_type, status, rule_key, version DESC)"
    )
    op.execute("ALTER TABLE company_rule_versions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE company_rule_versions FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON company_rule_versions
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON company_rule_versions TO contract_runtime")

    op.execute(
        """
        CREATE TABLE company_rule_events (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          rule_version_id uuid NOT NULL,
          actor_id uuid NOT NULL,
          event_type text NOT NULL CHECK (event_type IN (
            'created', 'updated', 'submitted', 'published', 'rejected', 'withdrawn', 'cloned')),
          note text NOT NULL CHECK (length(btrim(note)) BETWEEN 1 AND 1000),
          snapshot jsonb NOT NULL CHECK (jsonb_typeof(snapshot) = 'object'),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (tenant_id, id),
          FOREIGN KEY (tenant_id, rule_version_id)
            REFERENCES company_rule_versions (tenant_id, id),
          FOREIGN KEY (tenant_id, actor_id) REFERENCES app_users (tenant_id, actor_id)
        )
        """
    )
    op.execute("ALTER TABLE company_rule_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE company_rule_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON company_rule_events
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute(
        "CREATE INDEX company_rule_events_history_idx ON company_rule_events "
        "(tenant_id, rule_version_id, created_at DESC)"
    )
    op.execute("GRANT SELECT, INSERT ON company_rule_events TO contract_runtime")
    op.execute(
        """
        CREATE FUNCTION guard_company_rule_version_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF OLD.status = 'draft' AND NEW.status IN ('draft', 'review') THEN
            RETURN NEW;
          END IF;
          IF OLD.status = 'review' AND NEW.status IN ('review', 'draft', 'published') THEN
            IF NEW.status = 'published' AND NEW.reviewed_by = OLD.created_by THEN
              RAISE EXCEPTION 'company_rule_separation_of_duties_required'
                USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
          END IF;
          IF OLD.status = 'published' AND NEW.status = 'withdrawn'
             AND (NEW.rule_key, NEW.version, NEW.title, NEW.source_name, NEW.source_clause,
                  NEW.rule_type, NEW.threshold, NEW.role_scope, NEW.effective_from,
                  NEW.effective_until, NEW.content_sha256, NEW.created_by, NEW.reviewed_by,
                  NEW.published_by, NEW.published_at)
                 IS NOT DISTINCT FROM
                 (OLD.rule_key, OLD.version, OLD.title, OLD.source_name, OLD.source_clause,
                  OLD.rule_type, OLD.threshold, OLD.role_scope, OLD.effective_from,
                  OLD.effective_until, OLD.content_sha256, OLD.created_by, OLD.reviewed_by,
                  OLD.published_by, OLD.published_at) THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'company_rule_transition_invalid' USING ERRCODE = '23514';
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER company_rule_version_update_guard BEFORE UPDATE ON company_rule_versions "
        "FOR EACH ROW EXECUTE FUNCTION guard_company_rule_version_update()"
    )
    op.execute(
        """
        INSERT INTO app_role_permissions (tenant_id, role_id, permission_key)
        SELECT role.tenant_id, role.id, permission.permission_key
        FROM app_roles AS role
        JOIN (VALUES
          ('workspace_admin', 'company_rules.use'),
          ('workspace_admin', 'company_rules.manage'),
          ('contract_reviewer', 'company_rules.use')
        ) AS permission(role_key, permission_key)
          ON permission.role_key = role.role_key
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM app_role_permissions WHERE permission_key IN "
        "('company_rules.use', 'company_rules.manage')"
    )
    op.execute("DROP TABLE company_rule_events")
    op.execute("DROP TRIGGER company_rule_version_update_guard ON company_rule_versions")
    op.execute("DROP FUNCTION guard_company_rule_version_update()")
    op.execute("DROP TABLE company_rule_versions")
