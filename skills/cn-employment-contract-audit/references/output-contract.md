# 劳动人事输出契约

输出 `AuditSkillOutput`：`schema_version`、`skill_name`、`mode`、`review_context`、`timeline`、`evidence`、`check_results`、`calculations`、`human_tasks`、`limitations` 和 `summary`。

每个 `check_result` 至少包含：`rule_id`、`applicability`、`execution_status`、`judgment_status`、`severity`、`finding_type`、`fact_summary`、`evidence_refs`、`normative_reference_refs`、`proposal` 和 `human_task_refs`。

`judgment_status`：`conform`、`deviation`、`adverse`、`missing`、`relationship_uncertain`、`procedure_gap`、`conflicted`、`undetermined`、`human_review`、`not_applicable`。

`human_task.type`：`material`、`fact_investigation`、`calculation_review`、`hr_review`、`legal_review`、`approval`。解除场景必须列出拟采用理由、关键证据、程序、待付款和尚未解决事项。

精确结构见 [JSON Schema](audit-output.schema.json)。报告不得把建议或补签描述为已经修复历史风险。

