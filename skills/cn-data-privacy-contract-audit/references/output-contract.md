# 数据与隐私输出契约

输出 `AuditSkillOutput`：`schema_version`、`skill_name`、`mode`、`review_context`、`data_flow`、`evidence`、`check_results`、`human_tasks`、`limitations` 和 `summary`。

`data_flow` 节点和边至少表达主体、系统、数据类别、目的、操作、位置、下游、出境和保留/删除；实际角色与合同角色分别记录。

`judgment_status`：`conform`、`deviation`、`adverse`、`role_mismatch`、`scope_gap`、`security_capability_mismatch`、`crossborder_unverified`、`conflicted`、`undetermined`、`human_review`、`not_applicable`。

人工任务支持：`material`、`data_mapping`、`security_review`、`privacy_review`、`legal_review`、`approval`。数据出境、重要数据或行业叠加未核实时不得输出确定性通过。

精确结构见 [JSON Schema](audit-output.schema.json)。

