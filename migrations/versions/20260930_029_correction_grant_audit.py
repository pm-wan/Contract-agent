"""Record individual revoked grant ids for entity corrections.

Revision ID: 20260930_029
Revises: 20260930_028
"""

from alembic import op

revision = "20260930_029"
down_revision = "20260930_028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE case_legal_entity_correction_effects "
        "ADD COLUMN revoked_grant_ids uuid[] NOT NULL DEFAULT '{}'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE case_legal_entity_correction_effects DROP COLUMN revoked_grant_ids")
