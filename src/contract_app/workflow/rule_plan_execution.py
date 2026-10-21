"""Immutable record of a RulePlan assessment; never a dependency completion proof."""

import json
from hashlib import sha256
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RulePlanExecutionSnapshot(BaseModel):
    """Bind a deterministic RuleRun assessment to its frozen inputs and definition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["rule_plan_assessment"]
    input_snapshot_id: UUID
    fact_set_snapshot_id: UUID | None
    rule_id: str = Field(min_length=1, max_length=160)
    rule_version: str = Field(min_length=1, max_length=64)
    rule_source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    plan_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    facts_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    assessment_status: Literal[
        "not_applicable", "blocked", "undetermined", "incomplete", "needs_material", "human_review"
    ]
    blocked_by_checks: tuple[str, ...] = Field(max_length=100)
    missing_slots: tuple[str, ...] = Field(max_length=100)
    unresolved_minimum_judgment_indexes: tuple[int, ...] = Field(max_length=100)
    is_dependency_completion_proof: Literal[False]
    assessment_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def verify_assessment_binding(self) -> "RulePlanExecutionSnapshot":
        if (self.assessment_status == "blocked" and not self.blocked_by_checks) or any(
            index < 0 for index in self.unresolved_minimum_judgment_indexes
        ):
            raise ValueError("rule_plan_assessment_state_invalid")
        content = self.model_dump(mode="json", exclude={"assessment_sha256"})
        expected = sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()
        if self.assessment_sha256 != expected:
            raise ValueError("rule_plan_assessment_hash_mismatch")
        return self

    @classmethod
    def capture(
        cls,
        *,
        input_snapshot_id: UUID,
        fact_set_snapshot_id: UUID | None,
        rule_id: str,
        rule_version: str,
        rule_source_sha256: str,
        plan_sha256: str,
        facts_sha256: str,
        assessment_status: Literal[
            "not_applicable",
            "blocked",
            "undetermined",
            "incomplete",
            "needs_material",
            "human_review",
        ],
        blocked_by_checks: tuple[str, ...],
        missing_slots: tuple[str, ...],
        unresolved_minimum_judgment_indexes: tuple[int, ...],
    ) -> "RulePlanExecutionSnapshot":
        content = {
            "kind": "rule_plan_assessment",
            "input_snapshot_id": str(input_snapshot_id),
            "fact_set_snapshot_id": (
                str(fact_set_snapshot_id) if fact_set_snapshot_id is not None else None
            ),
            "rule_id": rule_id,
            "rule_version": rule_version,
            "rule_source_sha256": rule_source_sha256,
            "plan_sha256": plan_sha256,
            "facts_sha256": facts_sha256,
            "assessment_status": assessment_status,
            "blocked_by_checks": list(blocked_by_checks),
            "missing_slots": list(missing_slots),
            "unresolved_minimum_judgment_indexes": list(unresolved_minimum_judgment_indexes),
            "is_dependency_completion_proof": False,
        }
        digest = sha256(
            json.dumps(content, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                "utf-8"
            )
        ).hexdigest()
        return cls(
            kind="rule_plan_assessment",
            input_snapshot_id=input_snapshot_id,
            fact_set_snapshot_id=fact_set_snapshot_id,
            rule_id=rule_id,
            rule_version=rule_version,
            rule_source_sha256=rule_source_sha256,
            plan_sha256=plan_sha256,
            facts_sha256=facts_sha256,
            assessment_status=assessment_status,
            blocked_by_checks=blocked_by_checks,
            missing_slots=missing_slots,
            unresolved_minimum_judgment_indexes=unresolved_minimum_judgment_indexes,
            is_dependency_completion_proof=False,
            assessment_sha256=digest,
        )
