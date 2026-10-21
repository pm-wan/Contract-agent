"""Add the tenant-scoped legal entity registry."""

from alembic import op

revision = "20261018_035"
down_revision = "20261014_034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE legal_entities (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          legal_name text NOT NULL CHECK (length(btrim(legal_name)) BETWEEN 1 AND 255),
          unified_social_credit_code text,
          verification_basis text NOT NULL
            CHECK (length(btrim(verification_basis)) BETWEEN 1 AND 2000),
          is_active boolean NOT NULL DEFAULT true,
          created_by uuid NOT NULL,
          updated_by uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (tenant_id, id),
          FOREIGN KEY (tenant_id, created_by)
            REFERENCES app_users (tenant_id, actor_id),
          FOREIGN KEY (tenant_id, updated_by)
            REFERENCES app_users (tenant_id, actor_id)
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX legal_entities_credit_code_idx
          ON legal_entities (tenant_id, unified_social_credit_code)
          WHERE unified_social_credit_code IS NOT NULL
        """
    )
    op.execute(
        "CREATE INDEX legal_entities_name_idx ON legal_entities (tenant_id, lower(legal_name), id)"
    )
    op.execute("ALTER TABLE legal_entities ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE legal_entities FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON legal_entities
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON legal_entities TO contract_runtime")
    op.execute(
        """
        INSERT INTO app_role_permissions (tenant_id, role_id, permission_key)
        SELECT role.tenant_id, role.id, permission.permission_key
        FROM app_roles AS role
        JOIN (VALUES
          ('workspace_admin', 'entity_registry.use'),
          ('workspace_admin', 'entity_registry.manage'),
          ('contract_reviewer', 'entity_registry.use'),
          ('entity_admin', 'entity_registry.use'),
          ('entity_admin', 'entity_registry.manage')
        ) AS permission(role_key, permission_key)
          ON permission.role_key = role.role_key
        ON CONFLICT DO NOTHING
        """
    )
    op.execute(
        "UPDATE app_roles SET name = '主体管理员', "
        "description = '维护主体台账，并处理租户范围内的主体纠错。' "
        "WHERE role_key = 'entity_admin'"
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM app_role_permissions WHERE permission_key IN "
        "('entity_registry.use', 'entity_registry.manage')"
    )
    op.execute(
        "UPDATE app_roles SET name = '主体纠错管理员', "
        "description = '维护主体台账并处理租户范围内的已确认主体纠错。' "
        "WHERE role_key = 'entity_admin'"
    )
    op.execute("DROP TABLE legal_entities")
