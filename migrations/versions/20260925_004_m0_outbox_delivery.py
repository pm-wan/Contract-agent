"""Lease and retry durable outbox events.

Revision ID: 20260925_004
Revises: 20260925_003
"""

from alembic import op

revision = "20260925_004"
down_revision = "20260925_003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE outbox_events ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now()"
    )
    op.execute("ALTER TABLE outbox_events ADD COLUMN delivery_owner text")
    op.execute("ALTER TABLE outbox_events ADD COLUMN delivery_until timestamptz")
    op.execute(
        "ALTER TABLE outbox_events ADD COLUMN delivery_fencing_token bigint NOT NULL DEFAULT 0 "
        "CHECK (delivery_fencing_token >= 0)"
    )
    op.execute("ALTER TABLE outbox_events ADD COLUMN last_error text")
    op.execute("ALTER TABLE outbox_events ADD COLUMN dead_lettered_at timestamptz")
    op.execute(
        "ALTER TABLE outbox_events ADD CONSTRAINT outbox_delivery_lease_check "
        "CHECK ((delivery_owner IS NULL) = (delivery_until IS NULL))"
    )
    op.execute(
        "CREATE INDEX outbox_claim_idx ON outbox_events (next_attempt_at, created_at, id) "
        "WHERE published_at IS NULL AND dead_lettered_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX outbox_claim_idx")
    op.execute("ALTER TABLE outbox_events DROP CONSTRAINT outbox_delivery_lease_check")
    for column in (
        "dead_lettered_at",
        "last_error",
        "delivery_fencing_token",
        "delivery_until",
        "delivery_owner",
        "next_attempt_at",
    ):
        op.execute(f"ALTER TABLE outbox_events DROP COLUMN {column}")
