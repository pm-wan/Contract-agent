"""Add organization hierarchy, local accounts, sessions, and role permissions.

Revision ID: 20261014_034
Revises: 20260930_033
"""

from alembic import op

revision = "20261014_034"
down_revision = "20260930_033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE organization_units (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          parent_id uuid,
          code text NOT NULL CHECK (length(btrim(code)) BETWEEN 1 AND 64),
          name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
          is_active boolean NOT NULL DEFAULT true,
          created_by uuid,
          updated_by uuid,
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (tenant_id, id),
          UNIQUE (tenant_id, code),
          FOREIGN KEY (tenant_id, parent_id)
            REFERENCES organization_units (tenant_id, id),
          CHECK (parent_id IS NULL OR parent_id <> id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE app_users (
          tenant_id uuid NOT NULL,
          actor_id uuid NOT NULL DEFAULT gen_random_uuid(),
          username text NOT NULL CHECK (length(btrim(username)) BETWEEN 3 AND 80),
          username_key text NOT NULL CHECK (length(username_key) BETWEEN 3 AND 80),
          display_name text NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 120),
          organization_id uuid NOT NULL,
          password_hash text NOT NULL,
          is_active boolean NOT NULL DEFAULT true,
          must_change_password boolean NOT NULL DEFAULT false,
          created_by uuid,
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          last_login_at timestamptz,
          PRIMARY KEY (tenant_id, actor_id),
          UNIQUE (tenant_id, username_key),
          FOREIGN KEY (tenant_id, organization_id)
            REFERENCES organization_units (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE app_roles (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          role_key text NOT NULL CHECK (length(role_key) BETWEEN 1 AND 80),
          name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 120),
          description text NOT NULL DEFAULT '',
          is_system boolean NOT NULL DEFAULT false,
          is_active boolean NOT NULL DEFAULT true,
          created_by uuid,
          updated_by uuid,
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          PRIMARY KEY (tenant_id, id),
          UNIQUE (tenant_id, role_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE app_role_permissions (
          tenant_id uuid NOT NULL,
          role_id uuid NOT NULL,
          permission_key text NOT NULL CHECK (length(permission_key) BETWEEN 1 AND 120),
          PRIMARY KEY (tenant_id, role_id, permission_key),
          FOREIGN KEY (tenant_id, role_id) REFERENCES app_roles (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE app_user_roles (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          actor_id uuid NOT NULL,
          role_id uuid NOT NULL,
          granted_by uuid NOT NULL,
          granted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          revoked_by uuid,
          revoked_at timestamptz,
          PRIMARY KEY (tenant_id, id),
          FOREIGN KEY (tenant_id, actor_id) REFERENCES app_users (tenant_id, actor_id),
          FOREIGN KEY (tenant_id, role_id) REFERENCES app_roles (tenant_id, id),
          CHECK ((revoked_at IS NULL) = (revoked_by IS NULL))
        )
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX app_user_roles_active_idx
          ON app_user_roles (tenant_id, actor_id, role_id)
          WHERE revoked_at IS NULL
        """
    )
    op.execute(
        """
        CREATE TABLE app_sessions (
          tenant_id uuid NOT NULL,
          token_sha256 text NOT NULL CHECK (token_sha256 ~ '^[a-f0-9]{64}$'),
          actor_id uuid NOT NULL,
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          expires_at timestamptz NOT NULL,
          revoked_at timestamptz,
          PRIMARY KEY (tenant_id, token_sha256),
          FOREIGN KEY (tenant_id, actor_id) REFERENCES app_users (tenant_id, actor_id),
          CHECK (expires_at > created_at)
        )
        """
    )
    op.execute(
        "CREATE INDEX app_sessions_actor_idx ON app_sessions (tenant_id, actor_id, expires_at)"
    )

    for table in (
        "organization_units",
        "app_users",
        "app_roles",
        "app_role_permissions",
        "app_user_roles",
        "app_sessions",
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
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO contract_runtime")


def downgrade() -> None:
    for table in (
        "app_sessions",
        "app_user_roles",
        "app_role_permissions",
        "app_roles",
        "app_users",
        "organization_units",
    ):
        op.execute(f"DROP TABLE {table}")
