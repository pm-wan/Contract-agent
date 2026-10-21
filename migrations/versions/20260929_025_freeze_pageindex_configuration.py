"""Persist PageIndex configuration identity with indexes and frozen inputs.

Revision ID: 20260929_025
Revises: 20260928_024
"""

from alembic import op

revision = "20260929_025"
down_revision = "20260928_024"
branch_labels = None
depends_on = None

LEGACY_CONFIGURATION = '\'{"provider":"custom","configuration_status":"unversioned"}\'::jsonb'


def upgrade() -> None:
    for table in ("document_indexes", "input_snapshot_documents"):
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN pageindex_configuration jsonb NOT NULL "
            f"DEFAULT {LEGACY_CONFIGURATION}"
        )
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN pageindex_configuration_sha256 text NOT NULL "
            "DEFAULT repeat('0', 64)"
        )
        op.execute(
            f"ALTER TABLE {table} ADD CONSTRAINT {table}_pageindex_config_check CHECK "
            "(jsonb_typeof(pageindex_configuration) = 'object' AND "
            "pageindex_configuration_sha256 ~ '^[a-f0-9]{64}$')"
        )


def downgrade() -> None:
    for table in ("document_indexes", "input_snapshot_documents"):
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT {table}_pageindex_config_check")
        op.execute(f"ALTER TABLE {table} DROP COLUMN pageindex_configuration_sha256")
        op.execute(f"ALTER TABLE {table} DROP COLUMN pageindex_configuration")
