"""M0 case, run, durable task and outbox foundation.

Revision ID: 20260925_001
Revises:
"""

from alembic import op

revision = "20260925_001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        """
        CREATE TABLE audit_cases (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          title text NOT NULL CHECK (length(title) BETWEEN 1 AND 255),
          status text NOT NULL DEFAULT 'draft',
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE audit_runs (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          case_id uuid NOT NULL,
          status text NOT NULL DEFAULT 'queued' CHECK
            (status IN ('queued', 'running', 'waiting', 'completed', 'failed', 'cancelled')),
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, id),
          FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE tasks (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          run_id uuid NOT NULL,
          task_type text NOT NULL,
          payload jsonb NOT NULL DEFAULT '{}'::jsonb,
          status text NOT NULL DEFAULT 'queued' CHECK
            (status IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
          priority integer NOT NULL DEFAULT 0,
          attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
          next_run_at timestamptz NOT NULL DEFAULT now(),
          lease_owner text,
          lease_until timestamptz,
          fencing_token bigint NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
          idempotency_key text NOT NULL,
          last_error text,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, id),
          FOREIGN KEY (tenant_id, run_id) REFERENCES audit_runs (tenant_id, id),
          UNIQUE (tenant_id, task_type, idempotency_key),
          CHECK (status <> 'running' OR (lease_owner IS NOT NULL AND lease_until IS NOT NULL))
        )
        """
    )
    op.execute(
        """
        CREATE INDEX tasks_claim_idx ON tasks (priority DESC, next_run_at, id)
          WHERE status IN ('queued', 'running')
        """
    )
    op.execute(
        """
        CREATE TABLE task_results (
          tenant_id uuid NOT NULL,
          task_id uuid NOT NULL,
          run_id uuid NOT NULL,
          result jsonb NOT NULL,
          content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, task_id),
          FOREIGN KEY (tenant_id, task_id) REFERENCES tasks (tenant_id, id),
          FOREIGN KEY (tenant_id, run_id) REFERENCES audit_runs (tenant_id, id)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE outbox_events (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          task_id uuid NOT NULL,
          run_id uuid NOT NULL,
          event_type text NOT NULL,
          payload jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          published_at timestamptz,
          attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
          PRIMARY KEY (tenant_id, id),
          FOREIGN KEY (tenant_id, task_id) REFERENCES tasks (tenant_id, id),
          FOREIGN KEY (tenant_id, run_id) REFERENCES audit_runs (tenant_id, id),
          UNIQUE (tenant_id, task_id, event_type)
        )
        """
    )
    for table in ("audit_cases", "audit_runs", "tasks", "task_results", "outbox_events"):
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
    for table in ("outbox_events", "task_results", "tasks", "audit_runs", "audit_cases"):
        op.execute(f"DROP TABLE {table}")
