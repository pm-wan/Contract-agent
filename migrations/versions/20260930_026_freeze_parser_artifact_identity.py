"""Bind parser output identity to immutable input snapshots.

Revision ID: 20260930_026
Revises: 20260929_025
"""

from alembic import op

revision = "20260930_026"
down_revision = "20260929_025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE input_snapshot_documents ADD COLUMN parser_version text, "
        "ADD COLUMN parsed_sha256 text"
    )
    op.execute(
        "ALTER TABLE input_snapshot_documents ADD CONSTRAINT "
        "input_snapshot_documents_parsed_identity_check CHECK "
        "((parser_version IS NULL AND parsed_sha256 IS NULL) OR "
        "(parser_version IS NOT NULL AND length(parser_version) BETWEEN 1 AND 128 "
        "AND parsed_sha256 ~ '^[a-f0-9]{64}$'))"
    )
    op.execute(
        "GRANT INSERT (parser_version, parsed_sha256) ON input_snapshot_documents "
        "TO contract_runtime"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE INSERT (parser_version, parsed_sha256) ON input_snapshot_documents "
        "FROM contract_runtime"
    )
    op.execute(
        "ALTER TABLE input_snapshot_documents DROP CONSTRAINT "
        "input_snapshot_documents_parsed_identity_check"
    )
    op.execute(
        "ALTER TABLE input_snapshot_documents DROP COLUMN parsed_sha256, DROP COLUMN parser_version"
    )
