"""Allow the trusted case-creation endpoint to add its creator as a member.

Revision ID: 20260926_017
Revises: 20260926_016
"""

from alembic import op

revision = "20260926_017"
down_revision = "20260926_016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "GRANT INSERT (tenant_id, case_id, actor_id, role) ON case_members TO contract_runtime"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE INSERT (tenant_id, case_id, actor_id, role) ON case_members FROM contract_runtime"
    )
