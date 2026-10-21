"""Persist attachment relations in immutable input manifests.

Revision ID: 20260926_014
Revises: 20260926_013
"""

from alembic import op

revision = "20260926_014"
down_revision = "20260926_013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE input_snapshot_documents "
        "DROP CONSTRAINT input_snapshot_documents_relation_check"
    )
    op.execute(
        "ALTER TABLE input_snapshot_documents ADD CONSTRAINT "
        "input_snapshot_documents_relation_check "
        "CHECK (relation IN ('main', 'attachment', 'background', 'upstream'))"
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM input_snapshot_documents WHERE relation = 'attachment') THEN
            RAISE EXCEPTION 'cannot_downgrade_with_frozen_attachments';
          END IF;
        END $$
        """
    )
    op.execute(
        "ALTER TABLE input_snapshot_documents "
        "DROP CONSTRAINT input_snapshot_documents_relation_check"
    )
    op.execute(
        "ALTER TABLE input_snapshot_documents ADD CONSTRAINT "
        "input_snapshot_documents_relation_check "
        "CHECK (relation IN ('main', 'background', 'upstream'))"
    )
