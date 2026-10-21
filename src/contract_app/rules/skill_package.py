"""Deterministic development Skill candidates rendered from validated rules.

This renderer does not publish rules, resolve missing legal/enterprise knowledge,
or make a candidate eligible for production execution.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from contract_app.rules.compiler import load_development_rule_bytes

SKILL_GENERATOR_VERSION = "development-skill-renderer-v1"


@dataclass(frozen=True, slots=True)
class DevelopmentSkillPackage:
    rule_id: str
    rule_version: str
    rule_sha256: str
    tool_contract_sha256: str
    output_schema_sha256: str
    skill_markdown: str
    skill_sha256: str
    generator_version: str
    generated_at: datetime
    publication_blockers: tuple[str, ...]
    status: str = "development_candidate"


def render_development_skill(
    source: bytes,
    *,
    tool_contract: dict[str, object],
    output_schema: dict[str, object],
) -> DevelopmentSkillPackage:
    """Render validated rule and explicit model contracts without publishing it."""
    compiled = load_development_rule_bytes(source)
    data = yaml.safe_load(source)
    if not isinstance(data, dict):  # guarded by the compiler; keep this boundary explicit
        raise ValueError("development_rule_not_mapping")

    slots = data.get("slots")
    if not isinstance(slots, list):
        raise ValueError("development_rule_slots_invalid")
    _validate_tool_contract(tool_contract, compiled.tool_allowlist)
    try:
        Draft202012Validator.check_schema(output_schema)
    except SchemaError as exc:
        raise ValueError("skill_output_schema_invalid") from exc
    skill_definition = {
        "rule_id": compiled.rule_id,
        "version": compiled.version,
        "knowledge_domain": compiled.knowledge_domain,
        "basis_kind": compiled.basis_kind,
        "applicable_time_basis": compiled.applicable_time_basis,
        "review_owner_role": compiled.review_owner_role,
        "title": data.get("title"),
        "domain": data.get("domain"),
        "applicability": data.get("applicability"),
        "dependencies": data.get("dependencies"),
        "normative_refs": data.get("normative_refs"),
        "enterprise_position_refs": data.get("enterprise_position_refs"),
        "evidence_policy": {
            "direct_evidence_required": compiled.evidence_policy.direct_evidence_required,
            "absence_evidence": {
                "required_fields": list(compiled.evidence_policy.absence_required_fields)
            },
            "quote_hash_required": compiled.evidence_policy.quote_hash_required,
        },
        "slots": slots,
        "tool_allowlist": list(compiled.tool_allowlist),
        "invalidation_dependencies": list(compiled.invalidation_dependencies),
        "eval_suite_refs": list(compiled.eval_suite_refs),
        "tool_contract": tool_contract,
        "output_schema": output_schema,
    }
    tool_contract_sha256 = _contract_digest(tool_contract)
    output_schema_sha256 = _contract_digest(output_schema)
    rendered_json = json.dumps(
        skill_definition, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False
    )
    markdown = (
        "<!-- development-only: not approved or published for production use -->\n"
        f"# {data.get('title', compiled.rule_id)} · 开发态候选 Skill\n\n"
        "本文件由 RuleDefinition 确定性生成，仅供开发和评测。"
        "它不是法律意见、企业立场或正式审查结论。\n\n"
        "## 执行边界\n\n"
        "- 只按下方冻结定义提取候选事实与证据；不得自行增加、删除或跳过槽位。\n"
        "- 只可使用明确列出的工具；不得自行批准、阻断、回写或给出最终风险结论。\n"
        "- 缺少依据、材料、权限或工具结果时，按槽位允许状态表达不确定或转人工。\n"
        "- 本候选产物未完成发布审批，不得用于生产合同审查。\n\n"
        "## 规范化定义\n\n"
        "以下 JSON 是 RuleDefinition 的规范化运行说明；"
        "字段、条件、证据约束和工具白名单以此为准。\n\n"
        "```json\n"
        f"{rendered_json}\n"
        "```\n"
    )
    return DevelopmentSkillPackage(
        rule_id=compiled.rule_id,
        rule_version=compiled.version,
        rule_sha256=compiled.source_sha256,
        tool_contract_sha256=tool_contract_sha256,
        output_schema_sha256=output_schema_sha256,
        skill_markdown=markdown,
        skill_sha256=sha256(markdown.encode("utf-8")).hexdigest(),
        generator_version=SKILL_GENERATOR_VERSION,
        generated_at=datetime.now(UTC),
        publication_blockers=compiled.publication_blockers,
    )


def _contract_digest(value: dict[str, object]) -> str:
    try:
        serialized = json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("skill_contract_not_json") from exc
    return sha256(serialized.encode("utf-8")).hexdigest()


def _validate_tool_contract(
    tool_contract: dict[str, object], allowed_tools: tuple[str, ...]
) -> None:
    function = tool_contract.get("function")
    if not isinstance(function, dict) or not isinstance(function.get("name"), str):
        raise ValueError("skill_tool_contract_invalid")
    registered_name = function["name"]
    if not any(
        registered_name == allowed_name.rsplit(".", maxsplit=1)[-1]
        for allowed_name in allowed_tools
    ):
        raise ValueError("skill_tool_contract_not_allowlisted")
