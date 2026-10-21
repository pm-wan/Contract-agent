# 输出契约

## 1. 输出原则

默认输出一个 `AuditSkillOutput` JSON 对象。平台验证通过后，才可据此生成审查备忘录。完整字段约束见 `audit-output.schema.json`。

`judgment_status` 与执行状态分开：检查可以执行完成，但判断为缺材料、冲突或需人工。

## 2. 顶层结构

```json
{
  "schema_version": "1.0.0",
  "skill_name": "cn-procurement-contract-audit",
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

## 3. CheckResult 候选

每个规则一个结果：

```json
{
  "rule_id": "legal.liability.cap",
  "applicability": "applicable",
  "execution_status": "completed",
  "judgment_status": "deviation",
  "severity": "high",
  "finding_type": "adverse_clause",
  "reasoning_summary": "责任上限仅约束供应商的普通违约责任，多项核心责任被排除，导致上限保护范围有限。",
  "slot_candidates": [],
  "evidence_refs": ["ev-001", "ev-002"],
  "normative_reference_refs": [],
  "enterprise_position_refs": ["pos-liability-3.1"],
  "proposal": {
    "status": "suggested",
    "objective": "使核心赔偿责任纳入可接受的责任配置",
    "replacement_text": "……",
    "variables_requiring_confirmation": []
  },
  "human_task_refs": ["task-001"],
  "limitations": []
}
```

允许值：

- `applicability`：`applicable`、`not_applicable`、`undetermined`；
- `execution_status`：`completed`、`waiting_material`、`waiting_data`、`waiting_human`、`failed`；
- `judgment_status`：`conform`、`deviation`、`adverse`、`missing`、`conflicted`、`undetermined`、`human_review`、`not_applicable`；
- `finding_type`：`adverse_clause`、`missing_protection`、`inconsistency`、`material_gap`、`enterprise_deviation`、`favorable_term`、`no_issue_found`、`out_of_scope_observation`；
- `severity`：`critical`、`high`、`medium`、`low`、`info`、`undetermined`。

`no_issue_found` 只在适用规则已完成、所有关键槽位达到允许终态且证据门禁通过时使用。

## 4. SlotCandidate

```json
{
  "slot_id": "cap_basis",
  "slot_type": "fact",
  "status": "filled",
  "value": {
    "basis_type": "fees_paid_preceding_12_months",
    "verbatim": "引发索赔事件前十二个月内已支付的服务费"
  },
  "evidence_refs": ["ev-001"],
  "confidence": 0.94,
  "validation_notes": []
}
```

`confidence` 只表示候选抽取置信度，不能替代证据门禁或风险严重度。槽位终态使用：`filled`、`not_found`、`not_applicable`、`needs_material`、`data_unavailable`、`conflicted`、`undetermined`、`human_review`。

## 5. HumanTask

```json
{
  "task_id": "task-001",
  "type": "legal_review",
  "title": "确认责任例外的可接受范围",
  "reason": "合同偏离企业标准且无已批准例外",
  "required_role": "legal_owner",
  "related_rule_ids": ["legal.liability.cap"],
  "required_inputs": ["责任条款谈判底线"],
  "suggested_options": [
    {"option": "reject", "impact": "要求供应商将知识产权赔偿纳入约定上限"},
    {"option": "exception", "impact": "提交有权审批人接受例外"}
  ]
}
```

任务只能请求补材料、事实确认、法律复核或审批；不得伪造已作出的决定。

## 6. 覆盖和摘要

`coverage` 至少包含计划规则数、已完成数、等待材料数、等待人工数、失败数、证据可定位数及解析限制。`summary` 可包含各严重度发现数量和优先行动，但不得包含单一安全分。

若存在关键未决项，`summary.minimum_outcome` 必须为 `cannot_auto_pass`。只有人工决定对象可以表达最终 `accept`、`remediate`、`exception` 或 `block`。

## 7. 人类可读备忘录

需要生成备忘录时按以下顺序：

1. 文件头：内部保密、AI 辅助预审、待有权人员复核；
2. 审查对象与固定版本；
3. 底线摘要和 `minimum_outcome`；
4. 范围、必审覆盖与限制；
5. 已验证的关键发现；
6. 缺失保护和有利条款；
7. 缺材料、冲突和人工任务；
8. 标记为建议的修订文本；
9. 审批升级草稿；
10. 依据与证据索引。

备忘录不得把模型建议描述为合同现状、法律原文或已批准立场。
