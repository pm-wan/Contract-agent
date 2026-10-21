"""Conservative slot execution and evidence validation for development rules.

This runner never produces a legal or enterprise-policy pass. It validates model
candidates against frozen parsed text and routes unresolved work to a human.
"""

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID

from jsonschema import Draft202012Validator

from contract_app.documents.models import ParsedDocument, has_material_parser_warnings
from contract_app.immutable_json import freeze_json
from contract_app.rules.calculations import PaymentTotals, calculate_payment_totals
from contract_app.rules.compiler import (
    IMPLEMENTED_SLOT_VALIDATION_RULES,
    CompiledRiskRule,
    CompiledRule,
    CompiledSlot,
    RulePlan,
    RuleValidationError,
    canonical_rule_facts,
    evaluate_expression,
    rule_plan_fingerprint,
)
from contract_app.workflow.dependency_completion_proof import DependencyCompletionProof

SlotState = Literal["filled", "needs_material", "conflicted", "undetermined", "human_review"]
EvidenceKind = Literal["document_span", "absence_evidence", "calculation", "human_confirmation"]
AssessmentStatus = Literal[
    "not_applicable", "blocked", "undetermined", "incomplete", "needs_material", "human_review"
]
_DECIMAL_STRING = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_CURRENCY_CODE = re.compile(r"^[A-Z]{3}$")
_EVIDENCE_FIELDS = frozenset(
    {
        "document_version_id",
        "source_sha256",
        "page",
        "start_offset",
        "end_offset",
        "quote",
        "quote_sha256",
        "searched_sections",
        "query_terms",
        "parser_coverage",
        "supporting_quote_hashes",
    }
)
_EVIDENCE_FIELDS_BY_KIND: dict[EvidenceKind, frozenset[str]] = {
    "document_span": frozenset(
        {
            "document_version_id",
            "source_sha256",
            "page",
            "start_offset",
            "end_offset",
            "quote",
            "quote_sha256",
        }
    ),
    "absence_evidence": frozenset(
        {
            "document_version_id",
            "source_sha256",
            "searched_sections",
            "query_terms",
            "parser_coverage",
        }
    ),
    "calculation": frozenset({"supporting_quote_hashes"}),
    "human_confirmation": frozenset(),
}


def _freeze_slot_value(value: Any) -> Any:
    """Copy JSON values into deep-immutable containers before validation/storage."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        raise RuleValidationError("slot_value_not_json_compatible")
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise RuleValidationError("slot_value_not_json_compatible")
        for item in value.values():
            _freeze_slot_value(item)
        return freeze_json(value)
    if isinstance(value, (list, tuple)):
        for item in value:
            _freeze_slot_value(item)
        return freeze_json(value)
    raise RuleValidationError("slot_value_not_json_compatible")


@dataclass(frozen=True, slots=True)
class RuleEvidence:
    kind: EvidenceKind
    document_version_id: UUID | None = None
    source_sha256: str | None = None
    page: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None
    quote: str | None = None
    quote_sha256: str | None = None
    searched_sections: tuple[str, ...] = ()
    query_terms: tuple[str, ...] = ()
    parser_coverage: str | None = None
    supporting_quote_hashes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in (
            "document_span",
            "absence_evidence",
            "calculation",
            "human_confirmation",
        ):
            raise RuleValidationError("evidence_identity_invalid")
        if self.document_version_id is not None and not isinstance(self.document_version_id, UUID):
            raise RuleValidationError("evidence_identity_invalid")
        for field_name in ("source_sha256", "quote", "quote_sha256", "parser_coverage"):
            value = getattr(self, field_name)
            if value is not None and not isinstance(value, str):
                raise RuleValidationError("evidence_identity_invalid")
        for field_name in ("page", "start_offset", "end_offset"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                raise RuleValidationError("evidence_coordinate_invalid")
        for field_name in ("searched_sections", "query_terms", "supporting_quote_hashes"):
            values = getattr(self, field_name)
            if not isinstance(values, (tuple, list)) or any(
                not isinstance(value, str) for value in values
            ):
                raise RuleValidationError("evidence_scope_invalid")
            object.__setattr__(self, field_name, tuple(values))
        allowed_fields = _EVIDENCE_FIELDS_BY_KIND[self.kind]
        if any(
            field_name not in allowed_fields and getattr(self, field_name) not in (None, ())
            for field_name in _EVIDENCE_FIELDS
        ):
            raise RuleValidationError("evidence_field_not_allowed_for_kind")


@dataclass(frozen=True, slots=True)
class SlotSubmission:
    slot_id: str
    state: SlotState
    value: Any = None
    evidence: tuple[RuleEvidence, ...] = ()
    fact_type: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _freeze_slot_value(self.value))
        if not isinstance(self.evidence, (tuple, list)) or any(
            not isinstance(item, RuleEvidence) for item in self.evidence
        ):
            raise RuleValidationError("slot_evidence_invalid")
        object.__setattr__(self, "evidence", tuple(self.evidence))


@dataclass(frozen=True, slots=True)
class RuleAssessment:
    status: AssessmentStatus
    missing_slots: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    payment_totals: PaymentTotals | None = None
    matched_minimum_policy_rules: tuple[CompiledRiskRule, ...] = ()
    unresolved_minimum_judgment_indexes: tuple[int, ...] = ()


class RuleRun:
    __slots__ = ("_rule", "_plan", "_documents", "_facts", "_slots", "_submissions")

    def __init__(
        self,
        rule: CompiledRule,
        plan: RulePlan,
        documents: Mapping[UUID, ParsedDocument],
        *,
        facts: Mapping[str, Any] | None = None,
        completion_proofs: Mapping[str, DependencyCompletionProof] | None = None,
    ) -> None:
        if plan.rule_sha256 != rule.source_sha256:
            raise RuleValidationError("plan_rule_version_mismatch")
        canonical_facts = canonical_rule_facts({} if facts is None else facts)
        if sha256(canonical_facts.encode()).hexdigest() != plan.facts_sha256:
            raise RuleValidationError("plan_facts_mismatch")
        if (
            rule_plan_fingerprint(rule.source_sha256, canonical_facts, plan.completed_checks)
            != plan.plan_sha256
        ):
            raise RuleValidationError("plan_fingerprint_mismatch")
        proofs = {} if completion_proofs is None else dict(completion_proofs)
        if set(proofs) != set(plan.completed_checks):
            if plan.completed_checks:
                raise RuleValidationError("dependency_completion_proof_unimplemented")
            if proofs:
                raise RuleValidationError("dependency_completion_proof_unexpected")
        for check_id, proof in proofs.items():
            if proof.check_id != check_id or not proof.is_completion_proof:
                raise RuleValidationError("dependency_completion_proof_invalid")
        expected_plan = rule.plan(json.loads(canonical_facts), frozenset(plan.completed_checks))
        if plan != expected_plan:
            raise RuleValidationError("plan_content_mismatch")
        self._rule = rule
        self._plan = plan
        self._documents = MappingProxyType(dict(documents))
        self._facts: Mapping[str, Any] = freeze_json(json.loads(canonical_facts))
        self._slots = {slot.slot_id: slot for slot in rule.slots}
        self._submissions: dict[str, SlotSubmission] = {}

    @property
    def rule(self) -> CompiledRule:
        return self._rule

    @property
    def plan(self) -> RulePlan:
        return self._plan

    @property
    def documents(self) -> Mapping[UUID, ParsedDocument]:
        return self._documents

    def _accepted_values(self) -> dict[str, Any]:
        return {
            **self._facts,
            **{
                slot_id: submission.value
                for slot_id, submission in self._submissions.items()
                if submission.state == "filled"
            },
        }

    def _is_required(self, slot: CompiledSlot) -> bool:
        if not slot.required:
            return False
        condition = evaluate_expression(slot.required_when, self._accepted_values())
        # Unknown means still required; a model cannot skip a critical slot by
        # withholding or guessing the controlling fact.
        return condition is not False

    def _evaluate_minimum_judgments(
        self,
    ) -> tuple[tuple[CompiledRiskRule, ...], tuple[int, ...]]:
        values = self._accepted_values()
        matched: list[CompiledRiskRule] = []
        unresolved: list[int] = []
        for index, rule in enumerate(self.rule.minimum_judgment_rules):
            result = evaluate_expression(rule.when, values)
            if result is True:
                matched.append(rule)
            elif result is None:
                unresolved.append(index)
        return tuple(matched), tuple(unresolved)

    def submit(self, submission: SlotSubmission) -> None:
        if self.plan.applicability != "applicable":
            raise RuleValidationError("rule_plan_not_ready")
        slot = self._slots.get(submission.slot_id)
        if slot is None:
            raise RuleValidationError("slot_not_in_plan")
        if submission.slot_id in self._submissions:
            raise RuleValidationError("slot_already_submitted")
        if not self._is_required(slot):
            raise RuleValidationError("slot_not_required")
        for dependency in slot.dependencies:
            if dependency not in self._submissions and self._is_required(self._slots[dependency]):
                raise RuleValidationError("slot_dependency_unresolved")
        self.validate_candidate_submission(submission)
        self._submissions[submission.slot_id] = submission

    def validate_candidate_submission(self, submission: SlotSubmission) -> None:
        """Validate one extracted value/evidence pair without deciding applicability.

        This is for read-only candidate extraction when case facts do not yet
        establish that this rule applies. It never stores the submission or
        allows it to satisfy a required-slot plan.
        """
        slot = self._slots.get(submission.slot_id)
        if slot is None:
            raise RuleValidationError("slot_not_in_rule")
        if submission.state not in slot.allowed_terminal_states:
            raise RuleValidationError("slot_state_not_allowed")
        if slot.slot_type == "fact":
            if submission.state == "filled":
                if not slot.accepted_fact_types:
                    raise RuleValidationError("slot_fact_type_not_configured")
                if submission.fact_type is None:
                    raise RuleValidationError("slot_fact_type_required")
                if submission.fact_type not in slot.accepted_fact_types:
                    raise RuleValidationError("slot_fact_type_not_accepted")
            elif submission.fact_type is not None:
                raise RuleValidationError("nonfilled_slot_fact_type_invalid")
        elif submission.fact_type is not None:
            raise RuleValidationError("slot_fact_type_not_allowed")
        if submission.state == "filled":
            if not submission.evidence:
                raise RuleValidationError("filled_slot_requires_evidence")
            errors = list(Draft202012Validator(slot.value_schema).iter_errors(submission.value))
            if errors:
                raise RuleValidationError("slot_value_schema_invalid")
        elif submission.value is not None:
            raise RuleValidationError("nonfilled_slot_must_not_claim_value")
        for evidence in submission.evidence:
            if evidence.kind not in slot.evidence_kinds:
                raise RuleValidationError("evidence_kind_not_allowed")
            self._validate_evidence(evidence)
        if submission.state == "filled":
            unimplemented_rules = tuple(
                rule
                for rule in slot.validation_rules
                if rule not in IMPLEMENTED_SLOT_VALIDATION_RULES
            )
            if unimplemented_rules:
                raise RuleValidationError(
                    f"slot_validation_rule_unimplemented:{','.join(unimplemented_rules)}"
                )
            self._validate_slot_rules(slot, submission)
        if submission.slot_id == "payment_clause_present" and submission.state == "filled":
            expected_kind = "document_span" if submission.value is True else "absence_evidence"
            if not any(item.kind == expected_kind for item in submission.evidence):
                raise RuleValidationError("payment_clause_evidence_mismatch")

    @staticmethod
    def _validate_slot_rules(slot: CompiledSlot, submission: SlotSubmission) -> None:
        if "percentages_must_be_decimal_strings" in slot.validation_rules and (
            not isinstance(submission.value, list)
            or any(
                not isinstance(item, Mapping)
                or (
                    item.get("amount_type") == "percentage"
                    and (
                        not isinstance(item.get("percentage"), str)
                        or _DECIMAL_STRING.fullmatch(item["percentage"]) is None
                    )
                )
                for item in submission.value
            )
        ):
            raise RuleValidationError("percentage_must_be_decimal_string")
        if "fixed_amount_requires_currency" in slot.validation_rules and (
            not isinstance(submission.value, list)
            or any(
                not isinstance(item, Mapping)
                or (
                    item.get("amount_type") == "fixed"
                    and (
                        not isinstance(item.get("amount"), str)
                        or _DECIMAL_STRING.fullmatch(item["amount"]) is None
                        or not isinstance(item.get("currency"), str)
                        or _CURRENCY_CODE.fullmatch(item["currency"]) is None
                    )
                )
                for item in submission.value
            )
        ):
            raise RuleValidationError("fixed_payment_amount_requires_currency")

    def _validate_evidence(self, evidence: RuleEvidence) -> None:
        if evidence.kind == "document_span":
            document_id = evidence.document_version_id
            if (
                document_id is None
                or document_id not in self.documents
                or evidence.page is None
                or evidence.start_offset is None
                or evidence.end_offset is None
                or evidence.quote is None
                or (self.rule.evidence_policy.quote_hash_required and evidence.quote_sha256 is None)
            ):
                raise RuleValidationError("document_span_incomplete")
            document = self.documents[document_id]
            if evidence.source_sha256 != document.source_sha256:
                raise RuleValidationError("document_source_hash_mismatch")
            if not 1 <= evidence.page <= len(document.pages):
                raise RuleValidationError("document_page_invalid")
            page = document.pages[evidence.page - 1]
            if not 0 <= evidence.start_offset < evidence.end_offset <= len(page):
                raise RuleValidationError("document_offsets_invalid")
            if page[evidence.start_offset : evidence.end_offset] != evidence.quote:
                raise RuleValidationError("document_quote_mismatch")
            if evidence.quote_sha256 is not None and (
                sha256(evidence.quote.encode()).hexdigest() != evidence.quote_sha256
            ):
                raise RuleValidationError("document_quote_hash_mismatch")
        elif evidence.kind == "absence_evidence":
            document_id = evidence.document_version_id
            if (
                document_id is None
                or document_id not in self.documents
                or any(
                    not self._absence_field_is_valid(evidence, field)
                    for field in self.rule.evidence_policy.absence_required_fields
                )
            ):
                raise RuleValidationError("absence_scope_incomplete")
            document = self.documents[document_id]
            if evidence.source_sha256 != document.source_sha256 or has_material_parser_warnings(
                document.warnings
            ):
                raise RuleValidationError("absence_source_not_complete")
        elif evidence.kind == "calculation":
            existing_hashes = {
                item.quote_sha256
                for prior in self._submissions.values()
                for item in prior.evidence
                if item.kind == "document_span"
            }
            if not evidence.supporting_quote_hashes or not set(
                evidence.supporting_quote_hashes
            ).issubset(existing_hashes):
                raise RuleValidationError("calculation_provenance_invalid")
        else:
            # Named human decisions need their own persisted approval command.
            raise RuleValidationError("human_confirmation_not_implemented")

    @staticmethod
    def _absence_field_is_valid(evidence: RuleEvidence, field: str) -> bool:
        if field == "parser_coverage":
            return evidence.parser_coverage == "complete"
        values = (
            evidence.searched_sections if field == "searched_sections" else evidence.query_terms
        )
        return bool(values) and all(value.strip() for value in values)

    def assess(self) -> RuleAssessment:
        if self.plan.applicability == "not_applicable":
            return RuleAssessment("not_applicable")
        if self.plan.applicability == "undetermined":
            return RuleAssessment("undetermined", reasons=("applicability_unknown",))
        if self.plan.blocked_by_checks:
            return RuleAssessment("blocked", reasons=self.plan.blocked_by_checks)
        missing = tuple(
            slot.slot_id
            for slot in self.rule.slots
            if self._is_required(slot) and slot.slot_id not in self._submissions
        )
        schedule = self._submissions.get("payment_schedule")
        payment_totals = (
            calculate_payment_totals(schedule.value)
            if schedule is not None and schedule.state == "filled"
            else None
        )
        if missing:
            return RuleAssessment(
                "incomplete", missing_slots=missing, payment_totals=payment_totals
            )
        matched_judgments, unresolved_judgments = self._evaluate_minimum_judgments()
        policy_reasons = tuple(rule.reason for rule in matched_judgments)
        if unresolved_judgments:
            policy_reasons += ("minimum_judgment_condition_unknown",)
        clause = self._submissions.get("payment_clause_present")
        if clause is not None and clause.state == "filled" and clause.value is False:
            return RuleAssessment(
                "needs_material",
                reasons=("payment_clause_not_found", *policy_reasons),
                payment_totals=payment_totals,
                matched_minimum_policy_rules=matched_judgments,
                unresolved_minimum_judgment_indexes=unresolved_judgments,
            )
        required = (
            self._submissions[slot.slot_id] for slot in self.rule.slots if self._is_required(slot)
        )
        states = {submission.state for submission in required}
        if "needs_material" in states:
            return RuleAssessment(
                "needs_material",
                reasons=policy_reasons,
                payment_totals=payment_totals,
                matched_minimum_policy_rules=matched_judgments,
                unresolved_minimum_judgment_indexes=unresolved_judgments,
            )
        if "conflicted" in states or "human_review" in states:
            return RuleAssessment(
                "human_review",
                reasons=("enterprise_position_not_approved", *policy_reasons),
                payment_totals=payment_totals,
                matched_minimum_policy_rules=matched_judgments,
                unresolved_minimum_judgment_indexes=unresolved_judgments,
            )
        if "undetermined" in states:
            return RuleAssessment(
                "undetermined",
                reasons=policy_reasons,
                payment_totals=payment_totals,
                matched_minimum_policy_rules=matched_judgments,
                unresolved_minimum_judgment_indexes=unresolved_judgments,
            )
        return RuleAssessment(
            "human_review",
            reasons=(
                "enterprise_position_not_approved",
                *(policy_reasons or ("minimum_judgment_policy_no_match",)),
            ),
            payment_totals=payment_totals,
            matched_minimum_policy_rules=matched_judgments,
            unresolved_minimum_judgment_indexes=unresolved_judgments,
        )
