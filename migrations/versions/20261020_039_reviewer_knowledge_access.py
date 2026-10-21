"""Allow contract reviewers to read the shared review knowledge base."""

from alembic import op

revision = "20261020_039"
down_revision = "20261020_038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO app_role_permissions (tenant_id, role_id, permission_key)
        SELECT tenant_id, id, 'company_rules.use'
        FROM app_roles
        WHERE role_key = 'contract_reviewer' AND is_active
        ON CONFLICT DO NOTHING
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM app_role_permissions AS permission
        USING app_roles AS role
        WHERE permission.tenant_id = role.tenant_id
          AND permission.role_id = role.id
          AND permission.permission_key = 'company_rules.use'
          AND role.role_key = 'contract_reviewer'
        """
    )
