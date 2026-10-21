# 销售审查输出契约

默认输出 `AuditSkillOutput` JSON；通过 Schema 和证据门禁后才能生成备忘录、修订建议或业务摘要。完整约束见 `audit-output.schema.json`。

## 1. 顶层结构

```json
{
  "schema_version": "1.0.0",
  "skill_name": "cn-sales-contract-audit",
  "skill_version": "1.0.0",
  "mode": "full_review",
  "review_context": {},
  "coverage": {},
  "evidence": [],
  "check_results": [],
  "cross_clause_findings": [],
  "human_tasks": [],
  "limitations": [],
  "summary": {}
}
```

## 2. CheckResult

```json
{
  "rule_id": "legal.sales.liability_cap",
  "applicability": "applicable",
  "execution_status": "completed",
  "judgment_status": "adverse",
  "severity": "high",
  "finding_type": "adverse_clause",
  "reasoning_summary": "客户要求的责任例外覆盖主要风险类别，使合同总责任上限对我方保护有限。",
  "business_impacts": ["liability_exposure", "approval_required"],
  "slot_candidates": [],
  "evidence_refs": ["ev-001"],
  "normative_reference_refs": [],
  "enterprise_position_refs": ["sales-liability-v3"],
  "operational_capability_refs": [],
  "proposal": {
    "status": "suggested",
    "objective": "恢复有实质意义的累计责任上限",
    "preferred_text": "……",
    "fallback_texts": [],
    "variables_requiring_confirmation": []
  },
  "human_task_refs": ["task-001"],
  "limitations": []
}
```

允许状态：

- `applicability`：`applicable`、`not_applicable`、`undetermined`；
- `execution_status`：`completed`、`waiting_material`、`waiting_data`、`waiting_human`、`failed`；
- `judgment_status`：`conform`、`deviation`、`adverse`、`missing`、`capability_mismatch`、`conflicted`、`undetermined`、`human_review`、`not_applicable`；
- `finding_type`：`adverse_clause`、`missing_protection`、`inconsistency`、`material_gap`、`enterprise_deviation`、`capability_mismatch`、`favorable_term`、`no_issue_found`、`out_of_scope_observation`；
- `severity`：`critical`、`high`、`medium`、`low`、`info`、`undetermined`。

## 3. SlotCandidate

槽位终态为 `filled`、`not_found`、`not_applicable`、`needs_material`、`data_unavailable`、`conflicted`、`undetermined`、`human_review`。候选 `confidence` 不能替代证据门禁。

## 4. 修订和让步

`proposal.preferred_text` 是首选建议。`fallback_texts` 只有在已发布企业立场明确授权时才能出现，每项记录级别、交换条件、剩余风险和审批要求。建议文本不能描述为已接受、已批准或合同现状。

## 5. HumanTask

任务类型：`material`、`fact_review`、`capability_review`、`legal_review`、`commercial_decision`、`approval`。任务必须说明所需角色、原因、相关规则、所需输入、选项和期限；不能伪造决定。

## 6. 覆盖与摘要

`coverage` 展示计划规则、完成、等待材料、能力确认、人工和失败数量。若存在关键未决项，`summary.minimum_outcome` 必须为 `cannot_auto_pass`。

人类可读报告建议顺序：内部保密文件头、交易与版本、底线摘要、覆盖限制、关键发现、能力不匹配、首选/退让修订、审批任务、证据索引。客户版只包含对外建议文字和必要说明，不包含内部风险与审批信息。
