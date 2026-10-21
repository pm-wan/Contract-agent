-- 内部 MVP v1.0 物理约束基线。
-- 工程实施时由 goose 迁移承载；本文件用于评审和生成首版迁移。

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TYPE audit_case_status AS ENUM
  ('draft', 'ready', 'running', 'waiting', 'reviewed', 'decided', 'closed', 'cancelled');
CREATE TYPE execution_status AS ENUM
  ('queued', 'running', 'waiting_material', 'waiting_data', 'waiting_human',
   'retryable_failed', 'completed', 'failed', 'cancelled', 'superseded');
CREATE TYPE judgment_status AS ENUM
  ('passed', 'issue_found', 'needs_material', 'undetermined', 'not_applicable', 'human_review');
CREATE TYPE task_status AS ENUM
  ('queued', 'running', 'waiting', 'completed', 'failed', 'cancelled', 'superseded');

CREATE TABLE audit_cases (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  title text NOT NULL CHECK (length(title) BETWEEN 1 AND 255),
  purpose text NOT NULL CHECK (purpose = 'pre_review'),
  status audit_case_status NOT NULL DEFAULT 'draft',
  current_document_version_id uuid,
  current_run_id uuid,
  version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id)
);

CREATE TABLE document_versions (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  case_id uuid NOT NULL,
  role text NOT NULL CHECK (role IN ('primary_contract', 'attachment')),
  file_name text NOT NULL,
  media_type text NOT NULL CHECK (media_type IN
    ('application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document')),
  size_bytes bigint NOT NULL CHECK (size_bytes > 0),
  object_key text NOT NULL,
  content_hash text NOT NULL CHECK (content_hash ~ '^[a-f0-9]{64}$'),
  parser_version text,
  parsing_status text NOT NULL CHECK (parsing_status IN
    ('queued', 'parsing', 'completed', 'failed', 'needs_material')),
  page_count integer CHECK (page_count > 0),
  warnings jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(warnings) = 'array'),
  version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
  UNIQUE (tenant_id, case_id, content_hash, role)
);

CREATE TABLE input_snapshots (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  case_id uuid NOT NULL,
  primary_document_version_id uuid NOT NULL,
  attachment_version_ids uuid[] NOT NULL DEFAULT '{}',
  finance_fixture_snapshot_id uuid,
  contract_fixture_snapshot_id uuid,
  content_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
  FOREIGN KEY (tenant_id, primary_document_version_id) REFERENCES document_versions (tenant_id, id),
  UNIQUE (tenant_id, case_id, content_hash)
);

CREATE TABLE fact_set_snapshots (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  case_id uuid NOT NULL,
  revision integer NOT NULL CHECK (revision > 0),
  content_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
  UNIQUE (tenant_id, case_id, revision),
  UNIQUE (tenant_id, case_id, content_hash)
);

CREATE TABLE facts (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  case_id uuid NOT NULL,
  snapshot_id uuid NOT NULL,
  subject_ref text NOT NULL,
  predicate text NOT NULL,
  typed_value jsonb NOT NULL,
  unit text,
  currency text CHECK (currency IS NULL OR currency ~ '^[A-Z]{3}$'),
  tax_basis text,
  valid_time tstzrange,
  as_of timestamptz,
  observed_at timestamptz NOT NULL,
  verification_status text NOT NULL CHECK (verification_status IN
    ('candidate', 'verified', 'rejected', 'conflicted', 'superseded')),
  extractor_version text,
  version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
  FOREIGN KEY (tenant_id, snapshot_id) REFERENCES fact_set_snapshots (tenant_id, id)
);

CREATE TABLE audit_plan_snapshots (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  case_id uuid NOT NULL,
  rule_package_version text NOT NULL,
  plan jsonb NOT NULL CHECK (jsonb_typeof(plan) = 'object'),
  content_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
  UNIQUE (tenant_id, case_id, content_hash)
);

CREATE TABLE audit_runs (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  case_id uuid NOT NULL,
  input_snapshot_id uuid NOT NULL,
  fact_set_snapshot_id uuid NOT NULL,
  plan_snapshot_id uuid NOT NULL,
  rule_package_version text NOT NULL,
  execution_status execution_status NOT NULL DEFAULT 'queued',
  idempotency_key text NOT NULL,
  failure_code text,
  version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
  FOREIGN KEY (tenant_id, input_snapshot_id) REFERENCES input_snapshots (tenant_id, id),
  FOREIGN KEY (tenant_id, fact_set_snapshot_id) REFERENCES fact_set_snapshots (tenant_id, id),
  FOREIGN KEY (tenant_id, plan_snapshot_id) REFERENCES audit_plan_snapshots (tenant_id, id),
  UNIQUE (tenant_id, case_id, idempotency_key)
);

CREATE UNIQUE INDEX one_active_run_per_input
  ON audit_runs (tenant_id, case_id, input_snapshot_id)
  WHERE execution_status IN
    ('queued', 'running', 'waiting_material', 'waiting_data', 'waiting_human', 'retryable_failed');

CREATE TABLE check_results (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL,
  rule_id text NOT NULL,
  rule_version text NOT NULL,
  subject_ref text NOT NULL,
  judgment judgment_status NOT NULL,
  severity text NOT NULL CHECK (severity IN
    ('critical', 'high', 'medium', 'low', 'info', 'undetermined')),
  summary text NOT NULL,
  limitations jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(limitations) = 'array'),
  content_hash text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, run_id) REFERENCES audit_runs (tenant_id, id),
  UNIQUE (tenant_id, run_id, rule_id, subject_ref)
);

CREATE TABLE evidences (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  result_id uuid NOT NULL,
  kind text NOT NULL CHECK (kind IN
    ('document_span', 'source_record', 'calculation', 'normative_reference',
     'human_confirmation', 'absence_evidence')),
  support text NOT NULL CHECK (support IN
    ('direct', 'corroborating', 'context', 'contradicting')),
  source_version text NOT NULL,
  document_version_id uuid,
  page integer CHECK (page > 0),
  start_offset integer CHECK (start_offset >= 0),
  end_offset integer CHECK (end_offset >= start_offset),
  quote text,
  text_hash text,
  source_record_ref text,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, result_id) REFERENCES check_results (tenant_id, id),
  FOREIGN KEY (tenant_id, document_version_id) REFERENCES document_versions (tenant_id, id),
  CHECK (kind <> 'document_span' OR
    (document_version_id IS NOT NULL AND start_offset IS NOT NULL AND end_offset IS NOT NULL AND quote IS NOT NULL))
);

CREATE TABLE tasks (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  run_id uuid NOT NULL,
  task_type text NOT NULL,
  payload_ref text NOT NULL,
  status task_status NOT NULL DEFAULT 'queued',
  priority integer NOT NULL DEFAULT 0,
  attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
  next_run_at timestamptz NOT NULL DEFAULT now(),
  lease_owner text,
  lease_until timestamptz,
  fencing_token bigint NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
  idempotency_key text NOT NULL,
  last_error jsonb,
  version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, run_id) REFERENCES audit_runs (tenant_id, id),
  UNIQUE (tenant_id, task_type, idempotency_key),
  CHECK (status <> 'running' OR (lease_owner IS NOT NULL AND lease_until IS NOT NULL))
);

CREATE INDEX tasks_claim_idx ON tasks (priority DESC, next_run_at, id)
  WHERE status IN ('queued', 'running');

CREATE TABLE human_tasks (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  case_id uuid NOT NULL,
  run_id uuid NOT NULL,
  task_type text NOT NULL CHECK (task_type IN
    ('material', 'fact_review', 'legal_review', 'approval', 'publish')),
  status text NOT NULL CHECK (status IN ('open', 'claimed', 'completed', 'cancelled', 'superseded')),
  title text NOT NULL,
  reason text NOT NULL,
  required_role text NOT NULL,
  assignee_id uuid,
  decision text CHECK (decision IS NULL OR decision IN
    ('accept', 'remediate', 'exception', 'block', 'provide_material', 'confirm_fact', 'escalate')),
  decision_reason text,
  valid_until timestamptz,
  version bigint NOT NULL DEFAULT 1 CHECK (version > 0),
  created_at timestamptz NOT NULL DEFAULT now(),
  created_by uuid NOT NULL,
  completed_at timestamptz,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
  FOREIGN KEY (tenant_id, run_id) REFERENCES audit_runs (tenant_id, id),
  CHECK (status <> 'completed' OR (decision IS NOT NULL AND decision_reason IS NOT NULL))
);

CREATE TABLE decision_snapshots (
  tenant_id uuid NOT NULL,
  id uuid NOT NULL DEFAULT gen_random_uuid(),
  case_id uuid NOT NULL,
  run_id uuid NOT NULL,
  decision text NOT NULL CHECK (decision IN ('accept', 'remediate', 'exception', 'block')),
  reason text NOT NULL,
  scope jsonb NOT NULL CHECK (jsonb_typeof(scope) = 'object'),
  valid_until timestamptz,
  result_set_hash text NOT NULL,
  decided_by uuid NOT NULL,
  decided_at timestamptz NOT NULL DEFAULT now(),
  content_hash text NOT NULL,
  PRIMARY KEY (tenant_id, id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
  FOREIGN KEY (tenant_id, run_id) REFERENCES audit_runs (tenant_id, id),
  UNIQUE (tenant_id, content_hash)
);

CREATE TABLE outbox_events (
  tenant_id uuid NOT NULL,
  event_id uuid NOT NULL DEFAULT gen_random_uuid(),
  case_id uuid NOT NULL,
  run_id uuid,
  event_type text NOT NULL,
  event_version integer NOT NULL DEFAULT 1 CHECK (event_version > 0),
  sequence bigint NOT NULL CHECK (sequence > 0),
  payload jsonb NOT NULL,
  occurred_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz,
  attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
  PRIMARY KEY (event_id),
  FOREIGN KEY (tenant_id, case_id) REFERENCES audit_cases (tenant_id, id),
  FOREIGN KEY (tenant_id, run_id) REFERENCES audit_runs (tenant_id, id),
  UNIQUE (tenant_id, case_id, sequence)
);

ALTER TABLE audit_cases ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_cases FORCE ROW LEVEL SECURITY;
ALTER TABLE document_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE document_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE input_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE input_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE fact_set_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE fact_set_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE facts ENABLE ROW LEVEL SECURITY;
ALTER TABLE facts FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_plan_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_plan_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE audit_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_runs FORCE ROW LEVEL SECURITY;
ALTER TABLE check_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE check_results FORCE ROW LEVEL SECURITY;
ALTER TABLE evidences ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidences FORCE ROW LEVEL SECURITY;
ALTER TABLE tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE tasks FORCE ROW LEVEL SECURITY;
ALTER TABLE human_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE human_tasks FORCE ROW LEVEL SECURITY;
ALTER TABLE decision_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE decision_snapshots FORCE ROW LEVEL SECURITY;
ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE outbox_events FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_cases ON audit_cases
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_isolation_documents ON document_versions
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_isolation_inputs ON input_snapshots
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_isolation_fact_sets ON fact_set_snapshots
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_isolation_facts ON facts
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_isolation_plans ON audit_plan_snapshots
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_isolation_runs ON audit_runs
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_isolation_results ON check_results
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_isolation_evidence ON evidences
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_isolation_tasks ON tasks
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_isolation_human_tasks ON human_tasks
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_isolation_decisions ON decision_snapshots
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
CREATE POLICY tenant_isolation_outbox ON outbox_events
  USING (tenant_id = current_setting('app.tenant_id')::uuid)
  WITH CHECK (tenant_id = current_setting('app.tenant_id')::uuid);
