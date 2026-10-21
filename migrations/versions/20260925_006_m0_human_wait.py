"""Persist fact-review waits and role-bound human decisions.

Revision ID: 20260925_006
Revises: 20260925_005
"""

from alembic import op

revision = "20260925_006"
down_revision = "20260925_005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE tasks DROP CONSTRAINT tasks_status_check")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT tasks_status_check CHECK "
        "(status IN ('queued', 'running', 'waiting_human', 'completed', 'failed', "
        "'cancelled', 'superseded'))"
    )
    op.execute(
        """
        CREATE TABLE human_tasks (
          tenant_id uuid NOT NULL,
          id uuid NOT NULL DEFAULT gen_random_uuid(),
          task_id uuid NOT NULL,
          run_id uuid NOT NULL,
          case_id uuid NOT NULL,
          type text NOT NULL CHECK (type = 'fact_review'),
          status text NOT NULL DEFAULT 'open' CHECK
            (status IN ('open', 'completed', 'cancelled', 'superseded')),
          reason_code text NOT NULL CHECK (reason_code ~ '^[a-z][a-z0-9_]{0,127}$'),
          required_role text NOT NULL CHECK (length(required_role) BETWEEN 1 AND 64),
          version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
          decision text CHECK (decision = 'confirm_fact'),
          decision_reason text CHECK (length(decision_reason) BETWEEN 1 AND 4000),
          decided_by uuid,
          decided_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, id),
          FOREIGN KEY (tenant_id, task_id) REFERENCES tasks (tenant_id, id),
          FOREIGN KEY (tenant_id, run_id) REFERENCES audit_runs (tenant_id, id),
          FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
          CHECK ((decision IS NULL AND decision_reason IS NULL AND decided_by IS NULL
                  AND decided_at IS NULL) OR
                 (decision IS NOT NULL AND decision_reason IS NOT NULL
                  AND decided_by IS NOT NULL AND decided_at IS NOT NULL)),
          CHECK (status <> 'completed' OR decision IS NOT NULL)
        )
        """
    )
    op.execute("ALTER TABLE human_tasks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE human_tasks FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON human_tasks
          USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON human_tasks TO contract_runtime")
    op.execute(
        "CREATE UNIQUE INDEX human_task_one_open_idx ON human_tasks (tenant_id, task_id) "
        "WHERE status = 'open'"
    )
    op.execute(
        "CREATE INDEX human_tasks_open_idx ON human_tasks (case_id, created_at, id) "
        "WHERE status = 'open'"
    )
    op.execute(
        "ALTER TABLE outbox_events DROP CONSTRAINT outbox_events_tenant_id_task_id_event_type_key"
    )
    op.execute(
        "CREATE UNIQUE INDEX outbox_task_terminal_once_idx ON outbox_events "
        "(tenant_id, task_id, event_type) "
        "WHERE event_type IN ('task.completed', 'task.failed')"
    )


def downgrade() -> None:
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM outbox_events GROUP BY "
        "tenant_id, task_id, event_type HAVING count(*) > 1) THEN "
        "RAISE EXCEPTION 'cannot downgrade duplicate outbox event types'; END IF; END $$"
    )
    op.execute("DROP INDEX outbox_task_terminal_once_idx")
    op.execute(
        "ALTER TABLE outbox_events ADD CONSTRAINT "
        "outbox_events_tenant_id_task_id_event_type_key "
        "UNIQUE (tenant_id, task_id, event_type)"
    )
    op.execute("DROP TABLE human_tasks")
    op.execute(
        "DO $$ BEGIN IF EXISTS (SELECT 1 FROM tasks WHERE status = 'waiting_human') THEN "
        "RAISE EXCEPTION 'cannot downgrade while human waits exist'; END IF; END $$"
    )
    op.execute("ALTER TABLE tasks DROP CONSTRAINT tasks_status_check")
    op.execute(
        "ALTER TABLE tasks ADD CONSTRAINT tasks_status_check CHECK "
        "(status IN ('queued', 'running', 'completed', 'failed', 'cancelled', 'superseded'))"
    )
