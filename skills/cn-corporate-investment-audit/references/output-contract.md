# 投资股权与治理输出契约

输出 `AuditSkillOutput`：`schema_version`、`skill_name`、`mode`、`review_context`、`cap_table`、`rights_matrix`、`evidence`、`check_results`、`calculations`、`human_tasks`、`limitations` 和 `summary`。

`cap_table` 必须说明交易前/后、基础或完全稀释口径、期权池、可转换工具、认缴/实缴和计算假设。`rights_matrix` 表达经济、表决、董事、否决、信息、转让和退出权。

`judgment_status`：`conform`、`deviation`、`adverse`、`cap_table_conflict`、`authority_gap`、`closing_gap`、`conflicted`、`undetermined`、`human_review`、`not_applicable`。

人工任务支持：`material`、`fact_review`、`calculation_review`、`tax_review`、`regulatory_review`、`legal_review`、`approval`。只有独立的 `DecisionSnapshot` 可表达最终批准。

精确结构见 [JSON Schema](audit-output.schema.json)。

