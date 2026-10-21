"""Deterministic, scenario-driven sales contract review for the development workbench.

This module is intentionally a bounded rule slice. It reports review candidates and
policy deviations; it never declares an entire contract legal or compliant.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any
from uuid import uuid4

LEGAL_RULES: tuple[dict[str, str], ...] = (
    {
        "rule_key": "legal.sales.deposit_limit",
        "title": "定金比例上限",
        "article": "《民法典》第586条",
        "summary": "定金数额不得超过主合同标的额的20%；超过部分不产生定金效力。",
        "source_url": "https://www.court.gov.cn/zixun/xiangqing/233181.html",
        "version": "中华人民共和国民法典（2021-01-01施行）",
    },
    {
        "rule_key": "legal.sales.standard_terms_notice",
        "title": "格式条款提示说明",
        "article": "《民法典》第496—497条",
        "summary": "审查重大利害关系条款是否合理提示说明，以及是否存在不合理免责、加重对方责任或排除主要权利情形。",
        "source_url": "https://www.cac.gov.cn/2020-06/01/c_15925617772683192.htm",
        "version": "中华人民共和国民法典（2021-01-01施行）",
    },
    {
        "rule_key": "legal.sales.disclaimer_limits",
        "title": "特定免责条款",
        "article": "《民法典》第506条",
        "summary": "免责条款涉及对方人身损害，或因故意、重大过失造成对方财产损失的，存在无效风险。",
        "source_url": "https://www.cac.gov.cn/2020-06/01/c_15925617772683192.htm",
        "version": "中华人民共和国民法典（2021-01-01施行）",
    },
    {
        "rule_key": "legal.sales.sale_terms_clarity",
        "title": "标的、价款与交付验收约定",
        "article": "《民法典》第596条、第620—621条",
        "summary": "检查销售标的、数量质量、价款、交付和检验约定是否清晰；缺项或矛盾属于待完善/待核对，不自动等同违法。",
        "source_url": "https://www.court.gov.cn/zixun/xiangqing/233181.html",
        "version": "中华人民共和国民法典（2021-01-01施行）",
    },
)

DEMO_COMPANY_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "demo-sales-discount-v1",
        "rule_key": "demo.sales.discount_approval",
        "version": 1,
        "title": "销售折扣审批线（示例）",
        "source_name": "销售价格管理办法（测试示例）",
        "source_clause": "示例口径：折扣超过15%须销售负责人审批。",
        "rule_type": "discount_limit",
        "threshold": "15",
        "role_scope": "seller",
        "effective_from": "2026-01-01",
        "effective_until": None,
        "status": "published",
        "is_demo": True,
        "content_sha256": "demo-only",
    },
    {
        "id": "demo-sales-credit-v1",
        "rule_key": "demo.sales.credit_term",
        "version": 1,
        "title": "销售账期审批线（示例）",
        "source_name": "销售授信管理办法（测试示例）",
        "source_clause": "示例口径：账期超过60天须财务审批。",
        "rule_type": "credit_days_limit",
        "threshold": "60",
        "role_scope": "seller",
        "effective_from": "2026-01-01",
        "effective_until": None,
        "status": "published",
        "is_demo": True,
        "content_sha256": "demo-only",
    },
    {
        "id": "demo-sales-amount-v1",
        "rule_key": "demo.sales.amount_authority",
        "version": 1,
        "title": "大额销售签批线（示例）",
        "source_name": "合同授权审批矩阵（测试示例）",
        "source_clause": "示例口径：合同金额达到100万元须区域负责人签批。",
        "rule_type": "approval_amount",
        "threshold": "1000000",
        "role_scope": "seller",
        "effective_from": "2026-01-01",
        "effective_until": None,
        "status": "published",
        "is_demo": True,
        "content_sha256": "demo-only",
    },
    {
        "id": "demo-sales-template-v1",
        "rule_key": "demo.sales.template_deviation",
        "version": 1,
        "title": "非标准条款审批（示例）",
        "source_name": "销售合同模板使用指引（测试示例）",
        "source_clause": "示例口径：偏离已批准销售模板的责任、付款或知识产权条款须法务会签。",
        "rule_type": "template_deviation_approval",
        "threshold": None,
        "role_scope": "seller",
        "effective_from": "2026-01-01",
        "effective_until": None,
        "status": "published",
        "is_demo": True,
        "content_sha256": "demo-only",
    },
)


def _scenario(
    scenario_id: str,
    title: str,
    category: str,
    description: str,
    contract_text: str,
    facts: dict[str, Any],
    evidence: dict[str, list[str]],
) -> dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "title": title,
        "category": category,
        "description": description,
        "contract_text": contract_text,
        "facts": facts,
        "evidence": evidence,
    }


SALES_SCENARIOS: tuple[dict[str, Any], ...] = (
    _scenario(
        "sales-clean",
        "条款齐全、内部阈值内",
        "positive",
        "验证无已配置风险项时的范围内结果。",
        "买方支付10%预付款（非定金），余款在交付验收后30日内支付。折扣10%。标的、数量、含税总价、交付地址、检验标准和异议期限均已写明。双方确认使用已批准模板。",
        {"role": "seller", "contract_date": "2026-10-19", "deposit_kind": "advance", "deposit_percent": 10, "standard_terms": True, "material_terms": True, "reasonable_notice": True, "exempts_personal_injury": False, "exempts_intentional_or_gross_negligence": False, "price_clear": True, "delivery_clear": True, "inspection_clear": True, "conflicting_terms": False, "discount_percent": 10, "credit_days": 30, "amount": 500000, "approval_obtained": True, "template_deviation": False, "template_approval": True},
        {"deposit": ["10%预付款（非定金）"], "terms": ["标的、数量、含税总价、交付地址、检验标准和异议期限均已写明。"], "policy": ["折扣10%", "30日内", "使用已批准模板"]},
    ),
    _scenario(
        "sales-deposit-25",
        "定金超过20%上限",
        "negative",
        "验证定金类型明确且比例超过法定上限时，输出限定范围的风险说明。",
        "双方约定买方支付合同总价25%作为定金，剩余价款按交付节点支付。",
        {"role": "seller", "contract_date": "2026-10-19", "deposit_kind": "deposit", "deposit_percent": 25, "standard_terms": False, "material_terms": False, "reasonable_notice": None, "exempts_personal_injury": False, "exempts_intentional_or_gross_negligence": False, "price_clear": True, "delivery_clear": True, "inspection_clear": True, "conflicting_terms": False, "discount_percent": 0, "credit_days": 0, "amount": 300000, "approval_obtained": True, "template_deviation": False, "template_approval": True},
        {"deposit": ["25%作为定金"]},
    ),
    _scenario(
        "sales-deposit-boundary",
        "定金恰为20%边界",
        "boundary",
        "验证边界值不被误判为超过20%。",
        "合同总价100万元，买方支付20万元作为定金，余款按约定支付。",
        {"role": "seller", "contract_date": "2026-10-19", "deposit_kind": "deposit", "deposit_percent": 20, "standard_terms": False, "material_terms": False, "reasonable_notice": None, "exempts_personal_injury": False, "exempts_intentional_or_gross_negligence": False, "price_clear": True, "delivery_clear": True, "inspection_clear": True, "conflicting_terms": False, "discount_percent": 0, "credit_days": 0, "amount": 1000000, "approval_obtained": True, "template_deviation": False, "template_approval": True},
        {"deposit": ["20万元作为定金"]},
    ),
    _scenario(
        "sales-advance-not-deposit",
        "高比例预付款但明确不是定金",
        "boundary",
        "验证预付款与定金不被混为一谈；如性质不明应转人工确认。",
        "买方支付合同价款30%作为预付款，用于备货，不具有定金性质。",
        {"role": "seller", "contract_date": "2026-10-19", "deposit_kind": "advance", "deposit_percent": 30, "standard_terms": False, "material_terms": False, "reasonable_notice": None, "exempts_personal_injury": False, "exempts_intentional_or_gross_negligence": False, "price_clear": True, "delivery_clear": True, "inspection_clear": True, "conflicting_terms": False, "discount_percent": 0, "credit_days": 0, "amount": 800000, "approval_obtained": True, "template_deviation": False, "template_approval": True},
        {"deposit": ["30%作为预付款", "不具有定金性质"]},
    ),
    _scenario(
        "sales-standard-no-notice",
        "格式条款未提示重大利害条款",
        "negative",
        "验证格式条款提示说明问题进入律师复核，而不是自动判定无效。",
        "本合同由卖方提供格式文本。卖方可单方变更交付计划，买方不得解除或要求退款。该条款未作加粗提示，亦未另行说明。",
        {"role": "seller", "contract_date": "2026-10-19", "deposit_kind": "none", "deposit_percent": None, "standard_terms": True, "material_terms": True, "reasonable_notice": False, "excludes_major_rights": True, "unreasonably_exempts": False, "exempts_personal_injury": False, "exempts_intentional_or_gross_negligence": False, "price_clear": True, "delivery_clear": True, "inspection_clear": True, "conflicting_terms": False, "discount_percent": 0, "credit_days": 30, "amount": 600000, "approval_obtained": True, "template_deviation": True, "template_approval": False},
        {"terms": ["卖方可单方变更交付计划，买方不得解除或要求退款。", "未作加粗提示，亦未另行说明。"], "policy": ["偏离已批准销售模板"]},
    ),
    _scenario(
        "sales-disclaimer-invalidity-risk",
        "免责条款涉及人身损害和重大过失",
        "negative",
        "验证《民法典》第506条明确列举的免责情形。",
        "无论任何原因造成买方人身损害，卖方均不承担责任；因卖方重大过失导致买方财产损失的，卖方亦不承担责任。",
        {"role": "seller", "contract_date": "2026-10-19", "deposit_kind": "none", "deposit_percent": None, "standard_terms": True, "material_terms": True, "reasonable_notice": True, "excludes_major_rights": False, "unreasonably_exempts": True, "exempts_personal_injury": True, "exempts_intentional_or_gross_negligence": True, "price_clear": True, "delivery_clear": True, "inspection_clear": True, "conflicting_terms": False, "discount_percent": 0, "credit_days": 30, "amount": 600000, "approval_obtained": True, "template_deviation": True, "template_approval": False},
        {"disclaimer": ["无论任何原因造成买方人身损害，卖方均不承担责任", "因卖方重大过失导致买方财产损失的，卖方亦不承担责任"], "policy": ["偏离已批准销售模板"]},
    ),
    _scenario(
        "sales-discount-approval",
        "折扣超过公司审批线",
        "company_policy",
        "验证企业规则独立产生审批偏离，不形成违法结论。",
        "合同含税总价100万元，销售折扣18%，折后价82万元。未提供销售负责人审批记录。",
        {"role": "seller", "contract_date": "2026-10-19", "deposit_kind": "none", "deposit_percent": None, "standard_terms": False, "material_terms": False, "reasonable_notice": None, "exempts_personal_injury": False, "exempts_intentional_or_gross_negligence": False, "price_clear": True, "delivery_clear": True, "inspection_clear": True, "conflicting_terms": False, "discount_percent": 18, "credit_days": 30, "amount": 820000, "approval_obtained": False, "template_deviation": False, "template_approval": True},
        {"policy": ["销售折扣18%", "未提供销售负责人审批记录"]},
    ),
    _scenario(
        "sales-credit-90",
        "销售账期超过60天",
        "company_policy",
        "验证长账期走财务审批提示，正常账期不触发企业规则。",
        "买方应在验收合格后90日内支付全部货款，未提供财务审批记录。",
        {"role": "seller", "contract_date": "2026-10-19", "deposit_kind": "none", "deposit_percent": None, "standard_terms": False, "material_terms": False, "reasonable_notice": None, "exempts_personal_injury": False, "exempts_intentional_or_gross_negligence": False, "price_clear": True, "delivery_clear": True, "inspection_clear": True, "conflicting_terms": False, "discount_percent": 0, "credit_days": 90, "amount": 400000, "approval_obtained": False, "template_deviation": False, "template_approval": True},
        {"policy": ["验收合格后90日内", "未提供财务审批记录"]},
    ),
    _scenario(
        "sales-large-no-signoff",
        "大额销售缺少授权签批",
        "company_policy",
        "验证金额边界采用达到阈值即要求签批。",
        "合同总金额为120万元，当前材料中未见区域负责人签批记录。",
        {"role": "seller", "contract_date": "2026-10-19", "deposit_kind": "none", "deposit_percent": None, "standard_terms": False, "material_terms": False, "reasonable_notice": None, "exempts_personal_injury": False, "exempts_intentional_or_gross_negligence": False, "price_clear": True, "delivery_clear": True, "inspection_clear": True, "conflicting_terms": False, "discount_percent": 0, "credit_days": 30, "amount": 1200000, "approval_obtained": False, "template_deviation": False, "template_approval": True},
        {"policy": ["合同总金额为120万元", "未见区域负责人签批记录"]},
    ),
    _scenario(
        "sales-clause-conflict",
        "合同正文与附件价款冲突",
        "conflict",
        "验证冲突证据同时展示并阻止自动作出确定判断。",
        "正文约定含税总价100万元。附件报价单约定含税总价120万元，未注明附件优先。",
        {"role": "seller", "contract_date": "2026-10-19", "deposit_kind": "none", "deposit_percent": None, "standard_terms": False, "material_terms": False, "reasonable_notice": None, "exempts_personal_injury": False, "exempts_intentional_or_gross_negligence": False, "price_clear": False, "delivery_clear": True, "inspection_clear": True, "conflicting_terms": True, "discount_percent": 0, "credit_days": 30, "amount": None, "approval_obtained": None, "template_deviation": False, "template_approval": True},
        {"conflict": ["正文约定含税总价100万元。", "附件报价单约定含税总价120万元，未注明附件优先。"]},
    ),
    _scenario(
        "sales-missing-material",
        "缺少价格和验收附件",
        "missing_material",
        "验证必要事实缺失时输出待补材料，不将未发现问题标成通过。",
        "合同仅写明“货款按双方后续确认金额支付”，正文没有验收标准或检验期限。",
        {"role": "seller", "contract_date": "2026-10-19", "deposit_kind": "unknown", "deposit_percent": None, "standard_terms": None, "material_terms": None, "reasonable_notice": None, "exempts_personal_injury": None, "exempts_intentional_or_gross_negligence": None, "price_clear": False, "delivery_clear": None, "inspection_clear": False, "conflicting_terms": None, "discount_percent": None, "credit_days": None, "amount": None, "approval_obtained": None, "template_deviation": None, "template_approval": None},
        {"price": ["货款按双方后续确认金额支付"], "inspection": ["正文没有验收标准或检验期限"]},
    ),
    _scenario(
        "sales-buyer-role",
        "我方作为买方，销售规则不适用",
        "not_applicable",
        "验证角色适用性：卖方销售政策不能错误套用于买方合同。",
        "我方作为买方向供应商采购货物。合同价款、交付和检验条款均明确。",
        {"role": "buyer", "contract_date": "2026-10-19", "deposit_kind": "advance", "deposit_percent": 10, "standard_terms": False, "material_terms": False, "reasonable_notice": None, "exempts_personal_injury": False, "exempts_intentional_or_gross_negligence": False, "price_clear": True, "delivery_clear": True, "inspection_clear": True, "conflicting_terms": False, "discount_percent": None, "credit_days": 30, "amount": 500000, "approval_obtained": True, "template_deviation": False, "template_approval": True},
        {"terms": ["合同价款、交付和检验条款均明确"]},
    ),
)

EXPECTED_SCENARIO_STATUSES: dict[str, dict[str, str]] = {
    "sales-clean": {
        "legal.sales.deposit_limit": "not_applicable",
        "legal.sales.sale_terms_clarity": "no_issue_found",
        "demo.sales.discount_approval": "no_issue_found",
    },
    "sales-deposit-25": {"legal.sales.deposit_limit": "risk_candidate"},
    "sales-deposit-boundary": {"legal.sales.deposit_limit": "no_issue_found"},
    "sales-advance-not-deposit": {"legal.sales.deposit_limit": "not_applicable"},
    "sales-standard-no-notice": {
        "legal.sales.standard_terms_notice": "human_review",
        "demo.sales.template_deviation": "policy_deviation",
    },
    "sales-disclaimer-invalidity-risk": {
        "legal.sales.disclaimer_limits": "risk_candidate",
        "legal.sales.standard_terms_notice": "human_review",
    },
    "sales-discount-approval": {"demo.sales.discount_approval": "policy_deviation"},
    "sales-credit-90": {"demo.sales.credit_term": "policy_deviation"},
    "sales-large-no-signoff": {"demo.sales.amount_authority": "policy_deviation"},
    "sales-clause-conflict": {
        "legal.sales.sale_terms_clarity": "undetermined",
        "demo.sales.amount_authority": "needs_material",
    },
    "sales-missing-material": {
        "legal.sales.deposit_limit": "undetermined",
        "legal.sales.standard_terms_notice": "needs_material",
        "legal.sales.sale_terms_clarity": "needs_material",
        "demo.sales.discount_approval": "needs_material",
    },
    "sales-buyer-role": {"demo.sales.discount_approval": "not_applicable"},
}


def list_scenarios() -> list[dict[str, Any]]:
    return [
        {
            **{key: value for key, value in scenario.items() if key != "evidence"},
            "expected_statuses": EXPECTED_SCENARIO_STATUSES[scenario["scenario_id"]],
        }
        for scenario in SALES_SCENARIOS
    ]


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _evidence(scenario: dict[str, Any], *keys: str) -> list[dict[str, str]]:
    values: list[dict[str, str]] = []
    for key in keys:
        for quote in scenario["evidence"].get(key, []):
            if quote in scenario["contract_text"]:
                values.append({"quote": quote, "source": scenario["scenario_id"]})
    return values


def _legal_check(
    rule_key: str,
    status: str,
    severity: str,
    summary: str,
    next_action: str,
    evidence: list[dict[str, str]],
) -> dict[str, Any]:
    rule = next(item for item in LEGAL_RULES if item["rule_key"] == rule_key)
    return {
        "domain": "legal",
        "rule_key": rule_key,
        "title": rule["title"],
        "status": status,
        "severity": severity,
        "summary": summary,
        "next_action": next_action,
        "evidence": evidence,
        "basis": {
            "title": rule["article"],
            "summary": rule["summary"],
            "source_url": rule["source_url"],
            "version": rule["version"],
        },
    }


def _policy_check(rule: dict[str, Any], status: str, summary: str, next_action: str, evidence: list[dict[str, str]]) -> dict[str, Any]:
    is_demo = bool(rule.get("is_demo"))
    return {
        "domain": "company_policy",
        "rule_key": rule["rule_key"],
        "title": rule["title"],
        "status": status,
        "severity": "medium" if status == "policy_deviation" else "low",
        "summary": summary,
        "next_action": next_action,
        "evidence": evidence,
        "basis": {
            "title": rule["source_name"],
            "summary": rule["source_clause"],
            "version": f"v{rule['version']}",
            "content_sha256": rule.get("content_sha256"),
            "demo_data": is_demo,
        },
    }


def _evaluate_legal(scenario: dict[str, Any], facts: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    deposit_kind = facts.get("deposit_kind")
    deposit_percent = _decimal(facts.get("deposit_percent"))
    if deposit_kind == "none":
        results.append(_legal_check("legal.sales.deposit_limit", "not_applicable", "none", "未识别到定金安排。", "无需按定金比例规则处理。", []))
    elif deposit_kind in {"deposit", "advance"} and deposit_percent is None:
        results.append(_legal_check("legal.sales.deposit_limit", "needs_material", "none", "已识别付款安排，但比例材料缺失。", "补充合同总价和对应付款金额；如款项性质不清，转法务确认。", _evidence(scenario, "deposit")))
    elif deposit_kind == "deposit" and deposit_percent is not None and deposit_percent > Decimal("20"):
        results.append(_legal_check("legal.sales.deposit_limit", "risk_candidate", "high", f"约定定金比例为{deposit_percent}%，超过20%上限的部分不产生定金效力；本结论不扩展判断其他条款效力。", "核对主合同标的额和款项定性，调整超出部分或由法务复核。", _evidence(scenario, "deposit")))
    elif deposit_kind == "deposit":
        results.append(_legal_check("legal.sales.deposit_limit", "no_issue_found", "none", f"在当前输入中，定金比例为{deposit_percent}%，未超过20%上限。", "仍需核对计算基数、条款整体和交易实质。", _evidence(scenario, "deposit")))
    elif deposit_kind == "advance":
        results.append(_legal_check("legal.sales.deposit_limit", "not_applicable", "none", "条款明确称为预付款而非定金；本规则不把预付款比例套用定金上限。", "如存在定金性质争议，转法务确认。", _evidence(scenario, "deposit")))
    else:
        results.append(_legal_check("legal.sales.deposit_limit", "undetermined", "none", "款项性质或比例无法确认。", "补充付款条款及金额依据，由法务确认属于定金还是预付款。", _evidence(scenario, "deposit")))

    standard = facts.get("standard_terms")
    if standard is False:
        results.append(_legal_check("legal.sales.standard_terms_notice", "not_applicable", "none", "当前输入未识别为格式条款。", "如实际文本由一方预先拟定并重复使用，请重新确认适用性。", []))
    elif standard is None:
        results.append(_legal_check("legal.sales.standard_terms_notice", "needs_material", "none", "是否属于格式条款尚未确认。", "确认文本是否由一方预先拟定并重复使用。", []))
    elif facts.get("material_terms") is None or facts.get("reasonable_notice") is None:
        results.append(_legal_check("legal.sales.standard_terms_notice", "needs_material", "none", "格式条款的重大利害影响或提示说明事实不完整。", "补充提示说明记录及条款对双方权利义务的影响，交法务复核。", _evidence(scenario, "terms")))
    elif facts.get("material_terms") and not facts.get("reasonable_notice"):
        results.append(_legal_check("legal.sales.standard_terms_notice", "human_review", "high", "存在格式条款且涉及重大利害关系，但当前材料未显示已合理提示说明；是否纳入合同需结合形成过程和具体证据判断。", "核对提示说明、磋商和签署证据，交法务确认。", _evidence(scenario, "terms")))
    elif facts.get("excludes_major_rights") or facts.get("unreasonably_exempts"):
        results.append(_legal_check("legal.sales.standard_terms_notice", "human_review", "high", "标准条款可能不合理地免除己方责任、加重对方责任或排除对方主要权利。", "核对交易背景、条款公平性和适用例外，交法务确认效力。", _evidence(scenario, "terms")))
    else:
        results.append(_legal_check("legal.sales.standard_terms_notice", "no_issue_found", "none", "当前输入未发现已配置的格式条款提示问题；不代表格式条款整体有效。", "签署前保留已完成提示说明的证据。", _evidence(scenario, "terms")))

    personal = facts.get("exempts_personal_injury")
    gross = facts.get("exempts_intentional_or_gross_negligence")
    if personal is None or gross is None:
        results.append(_legal_check("legal.sales.disclaimer_limits", "needs_material", "none", "免责条款范围尚未完整确认。", "补充完整责任限制和免责条款。", _evidence(scenario, "disclaimer")))
    elif personal or gross:
        results.append(_legal_check("legal.sales.disclaimer_limits", "risk_candidate", "critical", "免责表述涉及人身损害或故意、重大过失造成的财产损失，触及《民法典》第506条列明情形。", "保留原文并由法务确认条款范围及相关事实；不要据此自动签署或自动判定整份合同无效。", _evidence(scenario, "disclaimer")))
    else:
        results.append(_legal_check("legal.sales.disclaimer_limits", "no_issue_found", "none", "当前输入未命中本规则列明的免责情形。", "继续核对其他责任限制和具体交易事实。", _evidence(scenario, "disclaimer")))

    if facts.get("conflicting_terms") is True:
        results.append(_legal_check("legal.sales.sale_terms_clarity", "undetermined", "high", "正文与附件存在相互冲突的价款表述，当前无法确定适用口径。", "核实材料版本、效力顺序及双方确认记录后重新审核。", _evidence(scenario, "conflict")))
    elif any(facts.get(key) is None for key in ("price_clear", "delivery_clear", "inspection_clear")):
        results.append(_legal_check("legal.sales.sale_terms_clarity", "needs_material", "none", "价款、交付或检验事实有缺失，无法完整评估约定清晰度。", "补充报价/订单、交付和验收附件，并确认材料版本。", _evidence(scenario, "price", "inspection")))
    elif not all(facts.get(key) for key in ("price_clear", "delivery_clear", "inspection_clear")):
        missing = [name for name, key in (("价款", "price_clear"), ("交付", "delivery_clear"), ("检验验收", "inspection_clear")) if facts.get(key) is False]
        results.append(_legal_check("legal.sales.sale_terms_clarity", "drafting_risk", "medium", f"当前输入显示{('、'.join(missing))}约定缺失或不清晰；属于合同完善提示，不直接等同违法。", "补齐可执行的标的、金额、交付和检验约定，并核对附件。", _evidence(scenario, "price", "inspection")))
    else:
        results.append(_legal_check("legal.sales.sale_terms_clarity", "no_issue_found", "none", "当前输入未发现本规则覆盖的标的/价款/交付验收缺项。", "该结论只覆盖已配置字段。", _evidence(scenario, "terms")))
    return results


def _evaluate_policy(rule: dict[str, Any], scenario: dict[str, Any], facts: dict[str, Any]) -> dict[str, Any]:
    if rule.get("status") != "published":
        return _policy_check(rule, "not_applicable", "该制度版本尚未发布，不参与本次审核。", "完成审核发布后再纳入运行。", [])
    if rule.get("role_scope") not in {"either", facts.get("role")}:
        return _policy_check(rule, "not_applicable", f"本规则适用角色为{rule.get('role_scope')}，本合同当前角色为{facts.get('role')}。", "如角色判断不准确，请确认我方签约身份。", [])
    try:
        effective_from = date.fromisoformat(str(rule["effective_from"]))
        effective_until = date.fromisoformat(str(rule["effective_until"])) if rule.get("effective_until") else None
        contract_date = date.fromisoformat(str(facts["contract_date"]))
    except (KeyError, ValueError):
        return _policy_check(rule, "undetermined", "规则生效日期或合同日期不完整，无法判断适用版本。", "核对制度生效期间和合同日期。", [])
    if contract_date < effective_from or (effective_until is not None and contract_date > effective_until):
        return _policy_check(rule, "not_applicable", "合同日期不在该制度版本的有效期内。", "核对是否有适用的其他制度版本。", [])

    kind = rule.get("rule_type")
    threshold = _decimal(rule.get("threshold"))
    metric_key: str | None = None
    metric_label = ""
    evidence_key = "policy"
    requires_approval = False
    if kind == "discount_limit":
        metric_key, metric_label = "discount_percent", "折扣比例"
    elif kind == "credit_days_limit":
        metric_key, metric_label = "credit_days", "账期"
    elif kind == "approval_amount":
        metric_key, metric_label = "amount", "合同金额"
        requires_approval = True
    elif kind == "template_deviation_approval":
        if facts.get("template_deviation") is None:
            return _policy_check(rule, "needs_material", "未确认是否偏离已批准模板。", "补充模板比对结果。", _evidence(scenario, evidence_key))
        if not facts["template_deviation"]:
            return _policy_check(rule, "no_issue_found", "当前输入未识别模板偏离。", "保留模板版本及比对记录。", _evidence(scenario, evidence_key))
        if facts.get("template_approval") is None:
            return _policy_check(rule, "needs_material", "已发现模板偏离，但审批材料状态未知。", "补充法务会签/审批记录。", _evidence(scenario, evidence_key))
        status = "no_issue_found" if facts["template_approval"] else "policy_deviation"
        summary = "模板偏离已有审批记录。" if facts["template_approval"] else "发现非标准模板条款，当前材料中未见要求的法务会签记录。"
        return _policy_check(rule, status, summary, "归档审批记录；如尚未会签，提交法务审批。", _evidence(scenario, evidence_key))
    else:
        return _policy_check(rule, "undetermined", "该公司规则类型暂不支持自动执行。", "管理员核对规则配置。", [])

    value = _decimal(facts.get(metric_key)) if metric_key else None
    if value is None or threshold is None:
        return _policy_check(rule, "needs_material", f"{metric_label}或制度阈值缺失。", "补充合同数据并确认已发布制度中的阈值。", _evidence(scenario, evidence_key))
    if requires_approval:
        if value < threshold:
            return _policy_check(rule, "no_issue_found", f"合同金额{value}元低于签批阈值{threshold}元。", "该金额规则未触发。", _evidence(scenario, evidence_key))
        if facts.get("approval_obtained") is None:
            return _policy_check(rule, "needs_material", f"合同金额达到{threshold}元签批线，但审批材料状态未知。", "核对授权审批记录。", _evidence(scenario, evidence_key))
        status = "no_issue_found" if facts["approval_obtained"] else "policy_deviation"
        summary = "合同金额达到签批线，且已提供签批记录。" if facts["approval_obtained"] else f"合同金额{value}元达到{threshold}元签批线，未见所需授权记录。"
    else:
        exceeds = value > threshold
        status = "policy_deviation" if exceeds else "no_issue_found"
        unit = "%" if kind == "discount_limit" else "天"
        summary = f"{metric_label}{value}{unit}超过制度阈值{threshold}{unit}。" if exceeds else f"{metric_label}{value}{unit}未超过制度阈值{threshold}{unit}。"
    action = "补充对应审批记录；未审批的，按公司流程申请审批。" if status == "policy_deviation" else "当前输入未触发该公司规则。"
    return _policy_check(rule, status, summary, action, _evidence(scenario, evidence_key))


def evaluate_scenario(
    scenario_id: str,
    *,
    company_rules: list[dict[str, Any]] | None = None,
    fact_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scenario = next((item for item in SALES_SCENARIOS if item["scenario_id"] == scenario_id), None)
    if scenario is None:
        raise ValueError("sales_review_scenario_not_found")
    facts = {**scenario["facts"], **(fact_overrides or {})}
    legal = _evaluate_legal(scenario, facts)
    policies = list(company_rules or DEMO_COMPANY_RULES)
    enterprise = [_evaluate_policy(rule, scenario, facts) for rule in policies]
    checks = legal + enterprise
    for check in checks:
        for item in check["evidence"]:
            if item["quote"] not in scenario["contract_text"]:
                raise ValueError("sales_review_evidence_not_in_source")
    snapshot = {
        "scenario_id": scenario_id,
        "facts": facts,
        "legal_rule_versions": [
            {
                "rule_key": item["rule_key"],
                "article": item["article"],
                "version": item["version"],
                "source_url": item["source_url"],
            }
            for item in LEGAL_RULES
        ],
        "company_rule_versions": [
            {
                "rule_key": item["rule_key"],
                "version": item["version"],
                "content_sha256": item.get("content_sha256"),
            }
            for item in policies
        ],
    }
    snapshot_hash = sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    expected_statuses = EXPECTED_SCENARIO_STATUSES[scenario_id]
    actual_statuses = {item["rule_key"]: item["status"] for item in checks}
    mismatches = [
        {"rule_key": rule_key, "expected": expected, "actual": actual_statuses.get(rule_key)}
        for rule_key, expected in expected_statuses.items()
        if actual_statuses.get(rule_key) != expected
    ]
    return {
        "evaluation_id": str(uuid4()),
        "scenario_id": scenario_id,
        "evaluated_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": snapshot_hash,
        "facts": facts,
        "contract_text": scenario["contract_text"],
        "legal_checks": legal,
        "company_checks": enterprise,
        "summary": {
            "legal_risk_count": sum(item["status"] in {"risk_candidate", "human_review", "drafting_risk"} for item in legal),
            "company_deviation_count": sum(item["status"] == "policy_deviation" for item in enterprise),
            "needs_material_count": sum(item["status"] in {"needs_material", "undetermined"} for item in checks),
            "no_issue_found_count": sum(item["status"] == "no_issue_found" for item in checks),
            "test_data": True,
        },
        "company_rule_versions": [
            {"rule_key": item["rule_key"], "version": item["version"], "is_demo": bool(item.get("is_demo")), "content_sha256": item.get("content_sha256")}
            for item in policies
        ],
        "expected_statuses": expected_statuses,
        "scenario_verification": {
            "matches_expected": not mismatches,
            "mismatches": mismatches,
        },
        "limitations": [
            "本试验台使用内置结构化案例事实，不解析任意上传合同，也不自动识别案件中的真实制度。",
            "法律检查只覆盖页面列明的已配置规则；no_issue_found仅表示未命中该规则，不能解释为合同合法或可签署。",
            "公司规则标注为测试示例，正式使用前必须替换为公司批准的制度条文、适用范围、生效版本和审批记录。",
        ],
    }
