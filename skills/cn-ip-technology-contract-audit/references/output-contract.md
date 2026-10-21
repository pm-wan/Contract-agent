# 知识产权与技术合同输出契约

输出 `AuditSkillOutput`：`schema_version`、`skill_name`、`mode`、`review_context`、`rights_graph`、`evidence`、`check_results`、`human_tasks`、`limitations` 和 `summary`。

`rights_graph` 至少可表达：权利对象、原始贡献者、当前权利人、转让、许可、限制、第三方组件和下游使用。未知节点保留未知，不自动补齐。

每个结果包含：`rule_id`、适用性、执行和判断状态、严重度、事实、权利影响、证据、规范依据、修订建议和人工任务。

`judgment_status`：`conform`、`deviation`、`adverse`、`chain_gap`、`license_gap`、`third_party_risk`、`conflicted`、`undetermined`、`human_review`、`not_applicable`。

精确结构见 [JSON Schema](audit-output.schema.json)。不得把登记信息、权利保证或代码仓库中的单一证据描述为完整权利链。

