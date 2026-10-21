"""Require explicit verified project relation for upstream contract selection.

Revision ID: 20260925_003
Revises: 20260925_002
"""

from alembic import op

revision = "20260925_003"
down_revision = "20260925_002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE contract_selections ADD COLUMN relation_ref text")
    op.execute("ALTER TABLE contract_selections ADD COLUMN relation_verified_at timestamptz")
    # Existing unverified selections are kept for audit, but cannot stay active.
    op.execute(
        "UPDATE contract_selections SET revoked_at = now() "
        "WHERE relation = 'upstream' AND revoked_at IS NULL"
    )
    op.execute(
        """
        ALTER TABLE contract_selections
        ADD CONSTRAINT upstream_relation_verified_check
        CHECK (relation <> 'upstream' OR revoked_at IS NOT NULL OR
               (nullif(trim(relation_ref), '') IS NOT NULL
                AND relation_verified_at IS NOT NULL))
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE contract_selections DROP CONSTRAINT upstream_relation_verified_check")
    op.execute("ALTER TABLE contract_selections DROP COLUMN relation_verified_at")
    op.execute("ALTER TABLE contract_selections DROP COLUMN relation_ref")
