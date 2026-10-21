# 融资担保输出契约

输出 `AuditSkillOutput`：`schema_version`、`skill_name`、`mode`、`review_context`、`obligation_graph`、`evidence`、`check_results`、`calculations`、`human_tasks`、`limitations` 和 `summary`。

`obligation_graph` 表达主债权、提款、还款、利息费用、保证/担保、反担保、登记、顺位和执行关系。额度、余额和最高额不得混为同一数值。

`judgment_status`：`conform`、`deviation`、`adverse`、`authorization_gap`、`security_gap`、`perfection_gap`、`priority_uncertain`、`conflicted`、`undetermined`、`human_review`、`not_applicable`。

人工任务支持：`material`、`balance_confirmation`、`calculation_review`、`collateral_review`、`finance_review`、`legal_review`、`approval`。关键授权、主债权或登记不明确时不得自动通过。

精确结构见 [JSON Schema](audit-output.schema.json)。
