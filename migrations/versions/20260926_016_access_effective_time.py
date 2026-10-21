"""Persist the actual effective time of an access revocation.

Revision ID: 20260926_016
Revises: 20260926_015
"""

from alembic import op

revision = "20260926_016"
down_revision = "20260926_015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE document_access_events ADD COLUMN effective_at timestamptz")
    op.execute(
        "UPDATE document_access_events SET effective_at = created_at "
        "WHERE action = 'selection_revoked'"
    )
    op.execute(
        "ALTER TABLE document_access_events ADD CONSTRAINT document_access_effective_time_check "
        "CHECK ((action = 'selection_granted' AND effective_at IS NULL) OR "
        "(action = 'selection_revoked' AND effective_at IS NOT NULL))"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM document_access_events "
        "WHERE action = 'selection_revoked') THEN "
        "RAISE EXCEPTION 'cannot_downgrade_with_revocation_events'; END IF; END $$"
    )
    op.execute(
        "ALTER TABLE document_access_events DROP CONSTRAINT document_access_effective_time_check"
    )
    op.execute("ALTER TABLE document_access_events DROP COLUMN effective_at")
