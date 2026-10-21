"""Persist content-free execution trace events for each fenced task attempt.

Revision ID: 20260930_031
Revises: 20260930_030
"""

from alembic import op

revision = "20260930_031"
down_revision = "20260930_030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE task_execution_trace_events (
          tenant_id uuid NOT NULL,
          task_id uuid NOT NULL,
          run_id uuid NOT NULL,
          attempt integer NOT NULL CHECK (attempt > 0),
          sequence integer NOT NULL CHECK (sequence BETWEEN 1 AND 32),
          event_type text NOT NULL CHECK
            (event_type IN ('model_request', 'model_response', 'tool_exchange')),
          payload_sha256 text NOT NULL CHECK (payload_sha256 ~ '^[a-f0-9]{64}$'),
          payload_bytes integer NOT NULL CHECK (payload_bytes BETWEEN 0 AND 2000000),
          previous_event_sha256 text NOT NULL CHECK
            (previous_event_sha256 ~ '^[a-f0-9]{64}$'),
          event_sha256 text NOT NULL CHECK (event_sha256 ~ '^[a-f0-9]{64}$'),
          input_tokens bigint CHECK (input_tokens >= 0),
          output_tokens bigint CHECK (output_tokens >= 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, task_id, attempt, sequence),
          FOREIGN KEY (tenant_id, task_id) REFERENCES tasks (tenant_id, id),
          FOREIGN KEY (tenant_id, run_id) REFERENCES audit_runs (tenant_id, id),
          CHECK ((input_tokens IS NULL) = (output_tokens IS NULL)),
          CHECK (event_type = 'model_response' OR
            (input_tokens IS NULL AND output_tokens IS NULL))
        )
        """
    )
    op.execute("ALTER TABLE task_execution_trace_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE task_execution_trace_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON task_execution_trace_events
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute(
        "CREATE INDEX task_execution_trace_run_idx ON task_execution_trace_events "
        "(tenant_id, run_id, task_id, attempt, sequence)"
    )
    op.execute("GRANT SELECT, INSERT ON task_execution_trace_events TO contract_runtime")


def downgrade() -> None:
    op.execute("DROP TABLE task_execution_trace_events")
