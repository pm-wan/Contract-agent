"""Sales contract test workbench and tenant-scoped company rule library API."""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from contract_app.api_cases import get_database_url
from contract_app.api_errors import COMMON_PROBLEM_RESPONSES
from contract_app.api_snapshots import Principal, require_principal
from contract_app.auth_security import require_any_permission, require_permission, set_tenant
from contract_app.rules.sales_contract_review import (
    DEMO_COMPANY_RULES,
    LEGAL_RULES,
    evaluate_scenario,
    list_scenarios,
)

scenario_router = APIRouter(
    prefix="/api/v1/sales-contract-review",
    tags=["Sales contract review lab"],
    responses=COMMON_PROBLEM_RESPONSES,
)
company_rules_router = APIRouter(
    prefix="/api/v1/company-rules",
    tags=["Company rule library"],
    responses=COMMON_PROBLEM_RESPONSES,
)

RULE_TYPES = Literal[
    "discount_limit", "credit_days_limit", "approval_amount", "template_deviation_approval"
]
ROLE_SCOPES = Literal["seller", "buyer", "either"]
class ScenarioResponse(BaseModel):
    scenario_id: str
    title: str
    category: str
    description: str
    contract_text: str
    facts: dict[str, Any]
    expected_statuses: dict[str, str]


class EvaluateScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str = Field(min_length=1, max_length=80)


class CompanyRuleInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_key: str = Field(min_length=3, max_length=120, pattern=r"^[a-z][a-z0-9._-]{2,119}$")
    title: str = Field(min_length=1, max_length=160)
    source_name: str = Field(min_length=1, max_length=255)
    source_clause: str = Field(min_length=1, max_length=10000)
    rule_type: RULE_TYPES
    threshold: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4)
    role_scope: ROLE_SCOPES = "seller"
    effective_from: date
    effective_until: date | None = None

    @field_validator("rule_key")
    @classmethod
    def require_enterprise_sales_namespace(cls, value: str) -> str:
        if not value.startswith("enterprise.sales."):
            raise ValueError("company_rule_key_namespace_invalid")
        return value

    @field_validator("title", "source_name", "source_clause")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        result = value.strip()
        if not result:
            raise ValueError("text_required")
        return result

    @model_validator(mode="after")
    def validate_rule_configuration(self) -> "CompanyRuleInput":
        if self.effective_until is not None and self.effective_until < self.effective_from:
            raise ValueError("effective_date_range_invalid")
        if self.rule_type == "template_deviation_approval" and self.threshold is not None:
            raise ValueError("threshold_not_applicable")
        if self.rule_type != "template_deviation_approval" and self.threshold is None:
            raise ValueError("threshold_required")
        return self


class RuleActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("reason")
    @classmethod
    def trim_reason(cls, value: str) -> str:
        result = value.strip()
        if not result:
            raise ValueError("reason_required")
        return result


def _require_development_lab() -> None:
    if os.environ.get("APP_ENV") not in {"development", "test"}:
        raise HTTPException(status_code=404, detail="not_found")


def _serialize_rule(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for key in ("effective_from", "effective_until"):
        if result.get(key) is not None and isinstance(result[key], date):
            result[key] = result[key].isoformat()
    for key in ("created_at", "updated_at", "published_at", "withdrawn_at"):
        if result.get(key) is not None and isinstance(result[key], datetime):
            result[key] = result[key].isoformat()
    if result.get("threshold") is not None:
        result["threshold"] = str(result["threshold"])
    return result


def _content_hash(rule: dict[str, Any]) -> str:
    stable = {
        key: rule.get(key)
        for key in (
            "rule_key", "version", "contract_type", "title", "source_name", "source_clause",
            "rule_type", "threshold", "role_scope", "effective_from", "effective_until",
        )
    }
    normalized = {
        key: value.isoformat() if isinstance(value, date) else str(value) if isinstance(value, Decimal) else value
        for key, value in stable.items()
    }
    payload = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _record_event(
    connection: psycopg.Connection,
    *,
    tenant_id: UUID,
    rule_id: UUID,
    actor_id: UUID,
    event_type: str,
    note: str,
    snapshot: dict[str, Any],
) -> None:
    connection.execute(
        "INSERT INTO company_rule_events "
        "(tenant_id, rule_version_id, actor_id, event_type, note, snapshot) "
        "VALUES (%s, %s, %s, %s, %s, %s::jsonb)",
        (tenant_id, rule_id, actor_id, event_type, note, json.dumps(snapshot, ensure_ascii=False)),
    )


def _rule_select_sql() -> str:
    return """
        SELECT id, rule_key, version, contract_type, title, source_name, source_clause,
               rule_type, threshold, role_scope, effective_from, effective_until, status,
               content_sha256, created_by, reviewed_by, published_by, withdrawn_by,
               created_at, updated_at, published_at, withdrawn_at
        FROM company_rule_versions
    """


@scenario_router.get("/scenarios", response_model=list[ScenarioResponse], dependencies=[Depends(require_permission("cases.use"))])
def get_sales_scenarios() -> list[dict[str, Any]]:
    _require_development_lab()
    return list_scenarios()


@scenario_router.get("/catalog", dependencies=[Depends(require_permission("cases.use"))])
def get_sales_review_catalog() -> dict[str, Any]:
    _require_development_lab()
    return {
        "contract_type": "sales_contract",
        "contract_type_label": "销售合同",
        "legal_rules": list(LEGAL_RULES),
        "demo_company_rules": list(DEMO_COMPANY_RULES),
        "result_statuses": [
            "no_issue_found", "risk_candidate", "drafting_risk", "human_review",
            "policy_deviation", "needs_material", "undetermined", "not_applicable",
        ],
        "test_data": True,
    }


@scenario_router.post("/evaluate", dependencies=[Depends(require_permission("cases.use"))])
def evaluate_sales_scenario(
    body: EvaluateScenarioRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
) -> dict[str, Any]:
    _require_development_lab()
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        custom_rules = connection.execute(
            _rule_select_sql()
            + " WHERE tenant_id = %s AND status = 'published' AND contract_type = 'sales_contract' "
            + "ORDER BY rule_key, version DESC",
            (principal.tenant_id,),
        ).fetchall()
    scenario = next((item for item in list_scenarios() if item["scenario_id"] == body.scenario_id), None)
    contract_date = date.fromisoformat(str(scenario["facts"]["contract_date"])) if scenario else None
    active_custom_rules: list[dict[str, Any]] = []
    seen_rule_keys: set[str] = set()
    for row in custom_rules:
        if contract_date is None or row["effective_from"] > contract_date:
            continue
        if row["effective_until"] is not None and row["effective_until"] < contract_date:
            continue
        if row["rule_key"] in seen_rule_keys:
            continue
        active_custom_rules.append(_serialize_rule(row))
        seen_rule_keys.add(row["rule_key"])
    # The sandbox always includes clearly marked sample policies so scenarios work
    # on a fresh tenant; published tenant policies are evaluated alongside them.
    policies = [*DEMO_COMPANY_RULES, *active_custom_rules]
    try:
        return evaluate_scenario(
            body.scenario_id,
            company_rules=policies,
        )
    except ValueError as exc:
        if str(exc) == "sales_review_scenario_not_found":
            raise HTTPException(status_code=404, detail="sales_review_scenario_not_found") from exc
        raise HTTPException(status_code=422, detail="sales_review_evaluation_invalid") from exc


@company_rules_router.get(
    "",
    dependencies=[Depends(require_any_permission("company_rules.use", "company_rules.manage"))],
)
def list_company_rules(
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        rows = connection.execute(
            _rule_select_sql() + " WHERE tenant_id = %s ORDER BY rule_key, version DESC",
            (principal.tenant_id,),
        ).fetchall()
    return {
        "items": [*DEMO_COMPANY_RULES, *[_serialize_rule(row) for row in rows]],
        "legal_rules": list(LEGAL_RULES),
        "demo_notice": "带有“示例”标记的公司规则仅供测试，不代表本公司的正式制度。",
    }


@company_rules_router.post(
    "",
    status_code=201,
    dependencies=[Depends(require_permission("company_rules.manage"))],
)
def create_company_rule(
    body: CompanyRuleInput,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        current = connection.execute(
            "SELECT COALESCE(max(version), 0) AS version FROM company_rule_versions "
            "WHERE tenant_id = %s AND rule_key = %s",
            (principal.tenant_id, body.rule_key),
        ).fetchone()
        version = int(current["version"]) + 1 if current else 1
        rule_id = uuid4()
        row = connection.execute(
            """
            INSERT INTO company_rule_versions
              (tenant_id, id, rule_key, version, title, source_name, source_clause,
               rule_type, threshold, role_scope, effective_from, effective_until, status, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s)
            RETURNING id, rule_key, version, contract_type, title, source_name, source_clause,
              rule_type, threshold, role_scope, effective_from, effective_until, status,
              content_sha256, created_by, reviewed_by, published_by, withdrawn_by,
              created_at, updated_at, published_at, withdrawn_at
            """,
            (
                principal.tenant_id, rule_id, body.rule_key, version, body.title,
                body.source_name, body.source_clause, body.rule_type, body.threshold,
                body.role_scope, body.effective_from, body.effective_until, principal.actor_id,
            ),
        ).fetchone()
        assert row is not None
        serialized = _serialize_rule(row)
        _record_event(connection, tenant_id=principal.tenant_id, rule_id=rule_id,
                      actor_id=principal.actor_id, event_type="created", note="创建公司规则草稿。",
                      snapshot=serialized)
    return serialized


@company_rules_router.patch(
    "/{rule_version_id}",
    dependencies=[Depends(require_permission("company_rules.manage"))],
)
def update_company_rule(
    rule_version_id: UUID,
    body: CompanyRuleInput,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        result = connection.execute(
            """
            UPDATE company_rule_versions SET title = %s, source_name = %s, source_clause = %s,
              rule_type = %s, threshold = %s, role_scope = %s, effective_from = %s,
              effective_until = %s, updated_at = clock_timestamp()
            WHERE tenant_id = %s AND id = %s AND rule_key = %s AND status = 'draft'
              AND created_by = %s
            RETURNING id, rule_key, version, contract_type, title, source_name, source_clause,
              rule_type, threshold, role_scope, effective_from, effective_until, status,
              content_sha256, created_by, reviewed_by, published_by, withdrawn_by,
              created_at, updated_at, published_at, withdrawn_at
            """,
            (
                body.title, body.source_name, body.source_clause, body.rule_type,
                body.threshold, body.role_scope, body.effective_from, body.effective_until,
                principal.tenant_id, rule_version_id, body.rule_key, principal.actor_id,
            ),
        ).fetchone()
        if result is None:
            raise HTTPException(status_code=409, detail="company_rule_not_editable")
        serialized = _serialize_rule(result)
        _record_event(connection, tenant_id=principal.tenant_id, rule_id=rule_version_id,
                      actor_id=principal.actor_id, event_type="updated", note="更新公司规则草稿。",
                      snapshot=serialized)
    return serialized


@company_rules_router.post(
    "/{rule_version_id}/submit",
    dependencies=[Depends(require_permission("company_rules.manage"))],
)
def submit_company_rule(
    rule_version_id: UUID,
    body: RuleActionRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        result = connection.execute(
            _rule_select_sql()
            + " WHERE tenant_id = %s AND id = %s AND status = 'draft' AND created_by = %s FOR UPDATE",
            (principal.tenant_id, rule_version_id, principal.actor_id),
        ).fetchone()
        if result is None:
            raise HTTPException(status_code=409, detail="company_rule_not_submittable")
        snapshot = _serialize_rule(result)
        connection.execute(
            "UPDATE company_rule_versions SET status = 'review', updated_at = clock_timestamp() "
            "WHERE tenant_id = %s AND id = %s",
            (principal.tenant_id, rule_version_id),
        )
        _record_event(connection, tenant_id=principal.tenant_id, rule_id=rule_version_id,
                      actor_id=principal.actor_id, event_type="submitted", note=body.reason,
                      snapshot=snapshot)
    return {**snapshot, "status": "review"}


@company_rules_router.post(
    "/{rule_version_id}/approve",
    dependencies=[Depends(require_permission("company_rules.manage"))],
)
def approve_company_rule(
    rule_version_id: UUID,
    body: RuleActionRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        row = connection.execute(
            _rule_select_sql()
            + " WHERE tenant_id = %s AND id = %s AND status = 'review' FOR UPDATE",
            (principal.tenant_id, rule_version_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=409, detail="company_rule_not_approvable")
        if row["created_by"] == principal.actor_id:
            raise HTTPException(status_code=409, detail="separation_of_duties_required")
        digest = _content_hash(row)
        updated = connection.execute(
            """
            UPDATE company_rule_versions SET status = 'published', content_sha256 = %s,
              reviewed_by = %s, published_by = %s, published_at = clock_timestamp(),
              updated_at = clock_timestamp()
            WHERE tenant_id = %s AND id = %s
            RETURNING id, rule_key, version, contract_type, title, source_name, source_clause,
              rule_type, threshold, role_scope, effective_from, effective_until, status,
              content_sha256, created_by, reviewed_by, published_by, withdrawn_by,
              created_at, updated_at, published_at, withdrawn_at
            """,
            (digest, principal.actor_id, principal.actor_id, principal.tenant_id, rule_version_id),
        ).fetchone()
        assert updated is not None
        serialized = _serialize_rule(updated)
        _record_event(connection, tenant_id=principal.tenant_id, rule_id=rule_version_id,
                      actor_id=principal.actor_id, event_type="published", note=body.reason,
                      snapshot=serialized)
    return serialized


@company_rules_router.post(
    "/{rule_version_id}/reject",
    dependencies=[Depends(require_permission("company_rules.manage"))],
)
def reject_company_rule(
    rule_version_id: UUID,
    body: RuleActionRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        row = connection.execute(
            _rule_select_sql()
            + " WHERE tenant_id = %s AND id = %s AND status = 'review' FOR UPDATE",
            (principal.tenant_id, rule_version_id),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=409, detail="company_rule_not_rejectable")
        snapshot = _serialize_rule(row)
        connection.execute(
            "UPDATE company_rule_versions SET status = 'draft', updated_at = clock_timestamp() "
            "WHERE tenant_id = %s AND id = %s",
            (principal.tenant_id, rule_version_id),
        )
        _record_event(connection, tenant_id=principal.tenant_id, rule_id=rule_version_id,
                      actor_id=principal.actor_id, event_type="rejected", note=body.reason,
                      snapshot=snapshot)
    return {**snapshot, "status": "draft"}


@company_rules_router.post(
    "/{rule_version_id}/withdraw",
    dependencies=[Depends(require_permission("company_rules.manage"))],
)
def withdraw_company_rule(
    rule_version_id: UUID,
    body: RuleActionRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        updated = connection.execute(
            """
            UPDATE company_rule_versions SET status = 'withdrawn', withdrawn_by = %s,
              withdrawn_at = clock_timestamp(), updated_at = clock_timestamp()
            WHERE tenant_id = %s AND id = %s AND status = 'published'
            RETURNING id, rule_key, version, contract_type, title, source_name, source_clause,
              rule_type, threshold, role_scope, effective_from, effective_until, status,
              content_sha256, created_by, reviewed_by, published_by, withdrawn_by,
              created_at, updated_at, published_at, withdrawn_at
            """,
            (principal.actor_id, principal.tenant_id, rule_version_id),
        ).fetchone()
        if updated is None:
            raise HTTPException(status_code=409, detail="company_rule_not_withdrawable")
        serialized = _serialize_rule(updated)
        _record_event(connection, tenant_id=principal.tenant_id, rule_id=rule_version_id,
                      actor_id=principal.actor_id, event_type="withdrawn", note=body.reason,
                      snapshot=serialized)
    return serialized


@company_rules_router.post(
    "/{rule_version_id}/new-version",
    status_code=201,
    dependencies=[Depends(require_permission("company_rules.manage"))],
)
def clone_company_rule_version(
    rule_version_id: UUID,
    body: RuleActionRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        source = connection.execute(
            _rule_select_sql() + " WHERE tenant_id = %s AND id = %s",
            (principal.tenant_id, rule_version_id),
        ).fetchone()
        if source is None or source["status"] not in {"published", "withdrawn"}:
            raise HTTPException(status_code=409, detail="company_rule_version_not_cloneable")
        version_row = connection.execute(
            "SELECT max(version) AS version FROM company_rule_versions "
            "WHERE tenant_id = %s AND rule_key = %s",
            (principal.tenant_id, source["rule_key"]),
        ).fetchone()
        new_id = uuid4()
        new_version = int(version_row["version"]) + 1 if version_row and version_row["version"] else 1
        row = connection.execute(
            """
            INSERT INTO company_rule_versions
              (tenant_id, id, rule_key, version, title, source_name, source_clause, rule_type,
               threshold, role_scope, effective_from, effective_until, status, created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'draft', %s)
            RETURNING id, rule_key, version, contract_type, title, source_name, source_clause,
              rule_type, threshold, role_scope, effective_from, effective_until, status,
              content_sha256, created_by, reviewed_by, published_by, withdrawn_by,
              created_at, updated_at, published_at, withdrawn_at
            """,
            (
                principal.tenant_id, new_id, source["rule_key"], new_version, source["title"],
                source["source_name"], source["source_clause"], source["rule_type"],
                source["threshold"], source["role_scope"], source["effective_from"],
                source["effective_until"], principal.actor_id,
            ),
        ).fetchone()
        assert row is not None
        serialized = _serialize_rule(row)
        _record_event(connection, tenant_id=principal.tenant_id, rule_id=new_id,
                      actor_id=principal.actor_id, event_type="cloned", note=body.reason,
                      snapshot=serialized)
    return serialized


@company_rules_router.get(
    "/{rule_version_id}/events",
    dependencies=[Depends(require_any_permission("company_rules.use", "company_rules.manage"))],
)
def list_company_rule_events(
    rule_version_id: UUID,
    principal: Annotated[Principal, Depends(require_principal)],
    database_url: Annotated[str, Depends(get_database_url)],
) -> dict[str, Any]:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        rows = connection.execute(
            "SELECT id, rule_version_id, actor_id, event_type, note, snapshot, created_at "
            "FROM company_rule_events WHERE tenant_id = %s AND rule_version_id = %s "
            "ORDER BY created_at, id",
            (principal.tenant_id, rule_version_id),
        ).fetchall()
    return {"items": rows}
