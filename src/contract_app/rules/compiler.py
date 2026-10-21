"""Compile the development payment rule into a closed, reproducible slot plan.

No rule YAML is executed as code. This module does not publish a rule or make a
legal/enterprise judgment; missing dependencies remain explicit blockers.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from yaml.events import AliasEvent, CollectionEndEvent, CollectionStartEvent

from contract_app.bounded_files import read_bounded_file
from contract_app.immutable_json import freeze_json

ALLOWED_TOOLS = frozenset(
    {"document.locate_clause", "calculation.sum_decimal", "calculation.payment_ratio"}
)
ALLOWED_STATES = frozenset(
    {"filled", "needs_material", "conflicted", "undetermined", "human_review"}
)
ALLOWED_EVIDENCE_KINDS = frozenset(
    {"document_span", "absence_evidence", "calculation", "human_confirmation"}
)
ALLOWED_DETERMINISTIC_STEPS = frozenset(
    {
        "verify_input_snapshot",
        "locate_payment_candidates",
        "calculate_payment_totals",
        "apply_minimum_policy",
        "validate_evidence",
    }
)
ALLOWED_SEMANTIC_TASKS = frozenset({"extract_payment_schedule", "classify_payment_triggers"})
IMPLEMENTED_DETERMINISTIC_STEPS = frozenset(
    {
        "verify_input_snapshot",
        "locate_payment_candidates",
        "calculate_payment_totals",
        "apply_minimum_policy",
        "validate_evidence",
    }
)
# `contract_review` requests these through the frozen CandidateOutput contract,
# then validates each value and document-span citation before exposing a
# candidate. This records engineering coverage only; it does not assert model
# quality or clear the evaluation/publication gates.
IMPLEMENTED_SEMANTIC_TASKS = frozenset({"extract_payment_schedule", "classify_payment_triggers"})
SEMANTIC_TASK_SLOT_REQUIREMENTS = {
    "extract_payment_schedule": "payment_schedule",
    "classify_payment_triggers": "payment_triggers",
}
IMPLEMENTED_TOOLS = frozenset({"document.locate_clause", "calculation.sum_decimal"})
ALLOWED_RISK_JUDGMENTS = frozenset(
    {"issue_found", "needs_material", "undetermined", "human_review"}
)
ALLOWED_SEVERITIES = frozenset({"critical", "high", "medium", "low", "info"})
ALLOWED_SLOT_VALIDATION_RULES = frozenset(
    {
        "absence_requires_search_scope",
        "percentages_must_be_decimal_strings",
        "fixed_amount_requires_currency",
        "derive_with_decimal_executor_only",
        "judgment_must_follow_minimum_policy",
    }
)
APPLICABILITY_DIMENSION_FACTS = (
    ("contract_types", "contract.contract_type"),
    ("party_roles", "contract.party_role"),
    ("jurisdictions", "contract.jurisdiction"),
)
IMPLEMENTED_SLOT_VALIDATION_RULES = frozenset(
    {
        "absence_requires_search_scope",
        "percentages_must_be_decimal_strings",
        "fixed_amount_requires_currency",
    }
)
TOP_LEVEL_FIELDS = frozenset(
    {
        "rule_id",
        "version",
        "domain",
        "knowledge_domain",
        "basis_kind",
        "title",
        "status",
        "applicability",
        "dependencies",
        "normative_refs",
        "enterprise_position_refs",
        "applicable_time_basis",
        "review_owner_role",
        "slots",
        "execution",
        "risk_policy",
        "evidence_policy",
        "invalidation_dependencies",
        "eval_suite_refs",
    }
)
SLOT_FIELDS = frozenset(
    {
        "slot_id",
        "title",
        "slot_type",
        "value_schema",
        "required",
        "required_when",
        "critical",
        "dependencies",
        "accepted_fact_types",
        "evidence_policy",
        "validation_rules",
        "allowed_tools",
        "allowed_terminal_states",
        "invalidation_dependencies",
    }
)
APPLICABILITY_FIELDS = frozenset({"contract_types", "party_roles", "jurisdictions", "expression"})
DEPENDENCY_FIELDS = frozenset({"facts", "checks"})
EXECUTION_FIELDS = frozenset(
    {
        "deterministic_steps",
        "semantic_tasks",
        "tool_allowlist",
        "max_rounds",
        "timeout_seconds",
        "token_budget",
    }
)
RISK_POLICY_FIELDS = frozenset({"minimum_judgment_rules", "human_review_when"})
RISK_RULE_FIELDS = frozenset({"when", "judgment", "severity", "reason"})
EVIDENCE_POLICY_FIELDS = frozenset(
    {"direct_evidence_required", "absence_evidence", "quote_hash_required"}
)
ABSENCE_EVIDENCE_FIELDS = frozenset({"required_fields"})
ABSENCE_SCOPE_FIELDS = frozenset({"searched_sections", "parser_coverage", "query_terms"})
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]*$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
REFERENCE_VERSION = re.compile(r"(?:^|[-_])v?[0-9]+(?:\.[0-9]+)*$")
MAX_DEVELOPMENT_RULE_BYTES = 256 * 1024
MAX_YAML_NESTING_DEPTH = 64
MAX_YAML_PARSE_EVENTS = 500_000


class RuleValidationError(ValueError):
    """A development rule is malformed or requests unsupported execution."""


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _validate_yaml_source_structure(source: bytes) -> None:
    """Bound parser work and reject recursive aliases before object construction."""
    depth = 0
    event_count = 0
    active_anchors: set[str] = set()
    anchors_by_depth: dict[int, list[str]] = {}
    try:
        events = yaml.parse(source, Loader=yaml.SafeLoader)
        for event in events:
            event_count += 1
            if event_count > MAX_YAML_PARSE_EVENTS:
                raise yaml.YAMLError("yaml_parse_event_limit_exceeded")
            if isinstance(event, AliasEvent):
                if event.anchor in active_anchors:
                    raise yaml.YAMLError("yaml_recursive_alias_forbidden")
            elif isinstance(event, CollectionStartEvent):
                depth += 1
                if depth > MAX_YAML_NESTING_DEPTH:
                    raise yaml.YAMLError("yaml_nesting_depth_exceeded")
                if event.anchor is not None:
                    if event.anchor in active_anchors:
                        raise yaml.YAMLError("yaml_anchor_redefined_while_active")
                    active_anchors.add(event.anchor)
                    anchors_by_depth.setdefault(depth, []).append(event.anchor)
            elif isinstance(event, CollectionEndEvent):
                active_anchors.difference_update(anchors_by_depth.pop(depth, ()))
                depth -= 1
    except RecursionError as exc:
        raise yaml.YAMLError("yaml_parser_recursion_exceeded") from exc


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[Any, Any]:
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str) or key in result:
            raise RuleValidationError("duplicate_or_nonstring_yaml_key")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


def _mapping(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise RuleValidationError(f"{field}_must_be_mapping")
    return value


def _closed_mapping(value: Any, field: str, allowed: frozenset[str]) -> dict[str, Any]:
    result = _mapping(value, field)
    if unknown := result.keys() - allowed:
        raise RuleValidationError(f"unknown_{field}_fields:{','.join(sorted(unknown))}")
    return result


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuleValidationError(f"{field}_must_be_string_list")
    return tuple(value)


def _unique_nonempty_strings(value: Any, field: str) -> tuple[str, ...]:
    values = _string_list(value, field)
    if any(not item.strip() for item in values):
        raise RuleValidationError(f"{field}_contains_empty_string")
    if len(values) != len(set(values)):
        raise RuleValidationError(f"{field}_contains_duplicates")
    return values


def _validate_expression(value: Any) -> None:
    if isinstance(value, bool):
        return
    if not isinstance(value, dict) or len(value) != 1:
        raise RuleValidationError("expression_shape_invalid")
    operator, operands = next(iter(value.items()))
    if operator in {"all", "any"}:
        if not isinstance(operands, list) or not operands:
            raise RuleValidationError("expression_operands_invalid")
        for item in operands:
            _validate_expression(item)
        return
    if operator == "exists":
        if not isinstance(operands, str):
            raise RuleValidationError("exists_path_invalid")
        return
    if operator not in {"eq", "in", "gte", "lte"}:
        raise RuleValidationError("expression_operator_forbidden")
    if not isinstance(operands, list) or len(operands) != 2 or not isinstance(operands[0], str):
        raise RuleValidationError("expression_operands_invalid")
    if operator == "in" and not isinstance(operands[1], list):
        raise RuleValidationError("in_options_must_be_list")
    if operator in {"gte", "lte"} and _decimal(operands[1]) is None:
        raise RuleValidationError("numeric_comparison_target_invalid")


def _expression_references(value: Any) -> frozenset[str]:
    if isinstance(value, bool):
        return frozenset()
    operator, operands = next(iter(value.items()))
    if operator in {"all", "any"}:
        return frozenset(
            reference for item in operands for reference in _expression_references(item)
        )
    return frozenset({operands if operator == "exists" else operands[0]})


def _validate_expression_references(
    expression: Any, *, facts: frozenset[str], slot_ids: frozenset[str], field: str
) -> None:
    for reference in _expression_references(expression):
        slot_root = reference.split(".", maxsplit=1)[0]
        if reference not in facts and slot_root not in slot_ids:
            raise RuleValidationError(f"{field}_reference_undeclared:{reference}")


def _has_unresolved_slot_path(
    expression: Any,
    *,
    facts: frozenset[str],
    slot_schemas: Mapping[str, Mapping[str, Any]],
) -> bool:
    for reference in _expression_references(expression):
        if reference in facts:
            continue
        root, separator, suffix = reference.partition(".")
        if not separator or root not in slot_schemas:
            continue
        schema: Mapping[str, Any] = slot_schemas[root]
        traverses_array = False
        for segment in suffix.split("."):
            declared_type = schema.get("type")
            types = (
                {declared_type}
                if isinstance(declared_type, str)
                else set(declared_type)
                if isinstance(declared_type, list)
                else set()
            )
            if "array" in types:
                traverses_array = True
                items = schema.get("items")
                if not isinstance(items, Mapping):
                    return True
                schema = items
                declared_type = schema.get("type")
                types = (
                    {declared_type}
                    if isinstance(declared_type, str)
                    else set(declared_type)
                    if isinstance(declared_type, list)
                    else set()
                )
            if "object" not in types:
                return True
            properties = schema.get("properties")
            if not isinstance(properties, Mapping) or segment not in properties:
                return True
            child = properties[segment]
            if not isinstance(child, Mapping):
                return True
            schema = child
        if traverses_array:
            return True
    return False


def _decimal(value: Any) -> Decimal | None:
    """Parse a bounded JSON integer or decimal string; invalid facts are unknown."""
    if isinstance(value, (bool, float)) or not isinstance(value, (int, str)):
        return None
    try:
        text = str(value)
        if len(text) > 128:
            return None
        result = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


_EXPRESSION_MISSING = object()
_EXPRESSION_ARRAY_PATH = object()


def _expression_value(facts: Mapping[str, Any], path: str) -> Any:
    if path in facts:
        return facts[path]
    segments = path.split(".")
    current: Any = facts
    for segment in segments:
        if isinstance(current, list):
            return _EXPRESSION_ARRAY_PATH
        if not isinstance(current, Mapping) or segment not in current:
            return _EXPRESSION_MISSING
        current = current[segment]
    return current


def _same_json_kind(left: Any, right: Any) -> bool:
    """Whether two values have compatible JSON types for a rule comparison."""
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool)
    if isinstance(left, (int, float)) or isinstance(right, (int, float)):
        return (
            isinstance(left, (int, float))
            and not isinstance(left, bool)
            and isinstance(right, (int, float))
            and not isinstance(right, bool)
        )
    if isinstance(left, str) or isinstance(right, str):
        return isinstance(left, str) and isinstance(right, str)
    if isinstance(left, Mapping) or isinstance(right, Mapping):
        return isinstance(left, Mapping) and isinstance(right, Mapping)
    if isinstance(left, list) or isinstance(right, list):
        return isinstance(left, list) and isinstance(right, list)
    return type(left) is type(right)


def _json_value_equal(left: Any, right: Any) -> bool | None:
    """Compare JSON values without Python's bool/int equality aliasing."""
    if not _same_json_kind(left, right):
        return None
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if left.keys() != right.keys():
            return False
        results = [_json_value_equal(left[key], right[key]) for key in left]
        if False in results:
            return False
        return None if None in results else True
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return False
        results = [_json_value_equal(a, b) for a, b in zip(left, right, strict=True)]
        if False in results:
            return False
        return None if None in results else True
    if (
        isinstance(left, (int, float))
        and not isinstance(left, bool)
        and isinstance(right, (int, float))
        and not isinstance(right, bool)
    ):
        left_number = Decimal(str(left))
        right_number = Decimal(str(right))
        if not left_number.is_finite() or not right_number.is_finite():
            return None
        return left_number == right_number
    return bool(left == right)


def evaluate_expression(value: Any, facts: Mapping[str, Any]) -> bool | None:
    """Evaluate only the declarative operator set; None means insufficient facts."""
    _validate_expression(value)
    if isinstance(value, bool):
        return value
    operator, operands = next(iter(value.items()))
    if operator in {"all", "any"}:
        results = [evaluate_expression(item, facts) for item in operands]
        if operator == "all":
            return False if False in results else None if None in results else True
        return True if True in results else None if None in results else False
    if operator == "exists":
        actual = _expression_value(facts, operands)
        if actual is _EXPRESSION_ARRAY_PATH:
            return None
        return actual is not _EXPRESSION_MISSING and actual is not None
    field, target = operands
    actual = _expression_value(facts, field)
    if actual is _EXPRESSION_MISSING or actual is _EXPRESSION_ARRAY_PATH:
        return None
    if operator == "eq":
        return _json_value_equal(actual, target)
    if operator == "in":
        if not target:
            return False
        compatible_options = [item for item in target if _same_json_kind(actual, item)]
        if not compatible_options:
            return None
        results = [_json_value_equal(actual, item) for item in compatible_options]
        if True in results:
            return True
        return None if None in results else False
    actual_number = _decimal(actual)
    target_number = _decimal(target)
    if target_number is None:
        raise RuleValidationError("numeric_comparison_target_invalid")
    if actual_number is None:
        return None
    if operator == "gte":
        return actual_number >= target_number
    return actual_number <= target_number


@dataclass(frozen=True, slots=True)
class CompiledSlot:
    slot_id: str
    slot_type: Literal["evidence", "fact", "judgment"]
    dependencies: tuple[str, ...]
    invalidation_dependencies: tuple[str, ...]
    accepted_fact_types: tuple[str, ...]
    required: bool
    required_when: Any
    critical: bool
    value_schema: dict[str, Any]
    evidence_kinds: tuple[str, ...]
    quote_required: bool
    validation_rules: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    allowed_terminal_states: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompiledEvidencePolicy:
    direct_evidence_required: bool
    quote_hash_required: bool
    absence_required_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompiledRiskRule:
    when: Any
    judgment: str
    reason: str
    severity: str | None = None


@dataclass(frozen=True, slots=True)
class CompiledRule:
    rule_id: str
    version: str
    knowledge_domain: str | None
    basis_kind: str | None
    applicable_time_basis: str | None
    review_owner_role: str | None
    source_sha256: str
    normative_refs: tuple[str, ...]
    enterprise_position_refs: tuple[str, ...]
    invalidation_dependencies: tuple[str, ...]
    applicability_dimensions: tuple[tuple[str, tuple[str, ...]], ...]
    applicability_expression: Any
    evidence_policy: CompiledEvidencePolicy
    dependent_checks: tuple[str, ...]
    slots: tuple[CompiledSlot, ...]
    minimum_judgment_rules: tuple[CompiledRiskRule, ...]
    human_review_when: tuple[str, ...]
    eval_suite_refs: tuple[str, ...]
    tool_allowlist: tuple[str, ...]
    max_rounds: int
    timeout_seconds: int
    token_budget: int
    publication_blockers: tuple[str, ...]

    def plan(
        self, facts: Mapping[str, Any], completed_checks: frozenset[str] = frozenset()
    ) -> "RulePlan":
        """Build a deterministic plan from facts and declared completed-check IDs.

        Check completion remains a trusted application assertion, but foreign or
        malformed identifiers cannot enter the plan fingerprint silently.
        """
        if not isinstance(completed_checks, frozenset) or any(
            not isinstance(check, str) or not check for check in completed_checks
        ):
            raise RuleValidationError("rule_plan_completed_checks_invalid")
        unknown_checks = completed_checks.difference(self.dependent_checks)
        if unknown_checks:
            raise RuleValidationError("rule_plan_completed_check_unknown")
        canonical_facts = canonical_rule_facts(facts)
        normalized_facts = json.loads(canonical_facts)
        applicability_results: list[bool | None] = [
            evaluate_expression(self.applicability_expression, normalized_facts)
        ]
        for fact_name, allowed_values in self.applicability_dimensions:
            if not allowed_values or fact_name not in normalized_facts:
                applicability_results.append(None)
                continue
            value = normalized_facts[fact_name]
            applicability_results.append(
                value in allowed_values if isinstance(value, str) else None
            )
        applicability = (
            False
            if False in applicability_results
            else None
            if None in applicability_results
            else True
        )
        normalized_checks = tuple(sorted(completed_checks))
        missing_checks = tuple(
            check for check in self.dependent_checks if check not in completed_checks
        )
        return RulePlan(
            rule_sha256=self.source_sha256,
            plan_sha256=rule_plan_fingerprint(
                self.source_sha256, canonical_facts, normalized_checks
            ),
            facts_sha256=sha256(canonical_facts.encode()).hexdigest(),
            completed_checks=normalized_checks,
            applicability=(
                "applicable"
                if applicability is True
                else "not_applicable"
                if applicability is False
                else "undetermined"
            ),
            blocked_by_checks=missing_checks,
            potential_required_slots=tuple(slot.slot_id for slot in self.slots),
            publication_blockers=self.publication_blockers,
        )


@dataclass(frozen=True, slots=True)
class RulePlan:
    rule_sha256: str
    plan_sha256: str
    facts_sha256: str
    completed_checks: tuple[str, ...]
    applicability: Literal["applicable", "not_applicable", "undetermined"]
    blocked_by_checks: tuple[str, ...]
    potential_required_slots: tuple[str, ...]
    publication_blockers: tuple[str, ...]

    def __post_init__(self) -> None:
        sequence_fields = (
            "completed_checks",
            "blocked_by_checks",
            "potential_required_slots",
            "publication_blockers",
        )
        if any(
            not isinstance(values, tuple)
            or any(not isinstance(value, str) or not value for value in values)
            for values in (getattr(self, field) for field in sequence_fields)
        ):
            raise RuleValidationError("rule_plan_structure_invalid")
        if self.applicability not in {"applicable", "not_applicable", "undetermined"}:
            raise RuleValidationError("rule_plan_structure_invalid")


def _contains_float(value: Any) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, Mapping):
        return any(_contains_float(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_float(item) for item in value)
    return False


def _contains_non_string_mapping_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(not isinstance(key, str) for key in value) or any(
            _contains_non_string_mapping_key(item) for item in value.values()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_non_string_mapping_key(item) for item in value)
    return False


def canonical_rule_facts(facts: Mapping[str, Any]) -> str:
    """Return the canonical, finite JSON encoding used to bind rule facts."""
    if not isinstance(facts, Mapping):
        raise RuleValidationError("facts_must_be_mapping")
    if _contains_non_string_mapping_key(facts):
        raise RuleValidationError("facts_must_have_string_keys")
    if _contains_float(facts):
        raise RuleValidationError("facts_must_not_contain_float")
    try:
        return json.dumps(
            dict(facts),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RuleValidationError("facts_not_canonical_json") from exc


def rule_plan_fingerprint(
    rule_sha256: str, canonical_facts: str, completed_checks: tuple[str, ...]
) -> str:
    """Fingerprint the complete inputs that determine a rule execution plan."""
    try:
        canonical_plan = json.dumps(
            {
                "rule_sha256": rule_sha256,
                "facts": json.loads(canonical_facts),
                "completed_checks": list(completed_checks),
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise RuleValidationError("rule_plan_not_canonical_json") from exc
    return sha256(canonical_plan.encode()).hexdigest()


def _sort_slots(slots: dict[str, CompiledSlot]) -> tuple[CompiledSlot, ...]:
    ordered: list[CompiledSlot] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(slot_id: str) -> None:
        if slot_id in visiting:
            raise RuleValidationError("slot_dependency_cycle")
        if slot_id in visited:
            return
        if slot_id not in slots:
            raise RuleValidationError("slot_dependency_missing")
        visiting.add(slot_id)
        for dependency in slots[slot_id].dependencies:
            visit(dependency)
        visiting.remove(slot_id)
        visited.add(slot_id)
        ordered.append(slots[slot_id])

    for slot_id in slots:
        visit(slot_id)
    return tuple(ordered)


def load_development_rule(path: Path) -> CompiledRule:
    """Load only a development rule. Publication needs separate approvals and versions."""
    source = read_bounded_file(path, MAX_DEVELOPMENT_RULE_BYTES)
    if len(source) > MAX_DEVELOPMENT_RULE_BYTES:
        raise RuleValidationError("rule_file_too_large")
    return load_development_rule_bytes(source)


def load_development_rule_bytes(source: bytes) -> CompiledRule:
    """Compile exact rule bytes, allowing callers to bind execution to that content."""
    if len(source) > MAX_DEVELOPMENT_RULE_BYTES:
        raise RuleValidationError("rule_file_too_large")
    try:
        _validate_yaml_source_structure(source)
        document = yaml.load(source, Loader=_UniqueKeyLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise RuleValidationError("rule_yaml_invalid") from exc
    data = _mapping(document, "rule")
    if unknown := data.keys() - TOP_LEVEL_FIELDS:
        raise RuleValidationError(f"unknown_rule_fields:{','.join(sorted(unknown))}")
    rule_id, version = data.get("rule_id"), data.get("version")
    if not isinstance(rule_id, str) or not IDENTIFIER.fullmatch(rule_id):
        raise RuleValidationError("rule_id_invalid")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise RuleValidationError("rule_version_invalid")
    if (
        not isinstance(data.get("domain"), str)
        or not data["domain"].strip()
        or not isinstance(data.get("title"), str)
        or not data["title"].strip()
    ):
        raise RuleValidationError("rule_metadata_invalid")
    metadata_fields = (
        "knowledge_domain",
        "basis_kind",
        "applicable_time_basis",
        "review_owner_role",
    )
    metadata_values: dict[str, str | None] = {}
    for field in metadata_fields:
        value = data.get(field)
        if value is None:
            metadata_values[field] = None
        elif not isinstance(value, str) or not value.strip():
            raise RuleValidationError(f"{field}_invalid")
        else:
            metadata_values[field] = value
    if data.get("status") != "approved_for_development":
        raise RuleValidationError("rule_not_approved_for_development")
    applicability = _closed_mapping(
        data.get("applicability"), "applicability", APPLICABILITY_FIELDS
    )
    expression = applicability.get("expression")
    _validate_expression(expression)
    dependencies = _closed_mapping(data.get("dependencies"), "dependencies", DEPENDENCY_FIELDS)
    dependent_facts = _unique_nonempty_strings(dependencies.get("facts"), "dependent_facts")
    dependent_checks = _unique_nonempty_strings(dependencies.get("checks"), "dependent_checks")
    applicability_dimensions: list[tuple[str, tuple[str, ...]]] = []
    for field, fact_name in APPLICABILITY_DIMENSION_FACTS:
        allowed_values = _unique_nonempty_strings(
            applicability.get(field), f"applicability_{field}"
        )
        if allowed_values and fact_name not in dependent_facts:
            raise RuleValidationError(f"applicability_dimension_fact_not_declared:{fact_name}")
        applicability_dimensions.append((fact_name, allowed_values))
    _validate_expression_references(
        expression,
        facts=frozenset(dependent_facts),
        slot_ids=frozenset(),
        field="applicability",
    )
    normative_refs = _versioned_reference_list(
        data.get("normative_refs"), "normative_refs", namespace="legal."
    )
    enterprise_position_refs = _versioned_reference_list(
        data.get("enterprise_position_refs"),
        "enterprise_position_refs",
        namespace="enterprise.",
    )
    execution = _closed_mapping(data.get("execution"), "execution", EXECUTION_FIELDS)
    deterministic_steps = _unique_nonempty_strings(
        execution.get("deterministic_steps"), "deterministic_steps"
    )
    semantic_tasks = _unique_nonempty_strings(execution.get("semantic_tasks"), "semantic_tasks")
    if not set(deterministic_steps).issubset(ALLOWED_DETERMINISTIC_STEPS):
        raise RuleValidationError("deterministic_step_not_allowlisted")
    if not set(semantic_tasks).issubset(ALLOWED_SEMANTIC_TASKS):
        raise RuleValidationError("semantic_task_not_allowlisted")
    tools = _unique_nonempty_strings(execution.get("tool_allowlist"), "tool_allowlist")
    if not set(tools).issubset(ALLOWED_TOOLS):
        raise RuleValidationError("tool_not_allowlisted")
    for field, maximum in (("max_rounds", 10), ("timeout_seconds", 300), ("token_budget", 50000)):
        value = execution.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= maximum:
            raise RuleValidationError(f"{field}_invalid")
    risk_policy = _closed_mapping(data.get("risk_policy"), "risk_policy", RISK_POLICY_FIELDS)
    risk_rules = risk_policy.get("minimum_judgment_rules")
    if not isinstance(risk_rules, list):
        raise RuleValidationError("minimum_judgment_rules_must_be_list")
    compiled_risk_rules: list[CompiledRiskRule] = []
    for risk_rule in risk_rules:
        item = _closed_mapping(risk_rule, "minimum_judgment_rule", RISK_RULE_FIELDS)
        if not {"when", "judgment", "reason"}.issubset(item):
            raise RuleValidationError("minimum_judgment_rule_shape_invalid")
        _validate_expression(item["when"])
        if not isinstance(item["judgment"], str) or item["judgment"] not in ALLOWED_RISK_JUDGMENTS:
            raise RuleValidationError("minimum_judgment_rule_outcome_invalid")
        if not isinstance(item["reason"], str) or not item["reason"].strip():
            raise RuleValidationError("minimum_judgment_rule_reason_invalid")
        if "severity" in item and (
            not isinstance(item["severity"], str) or item["severity"] not in ALLOWED_SEVERITIES
        ):
            raise RuleValidationError("minimum_judgment_rule_severity_invalid")
        compiled_risk_rules.append(
            CompiledRiskRule(
                when=freeze_json(item["when"]),
                judgment=item["judgment"],
                reason=item["reason"],
                severity=item.get("severity"),
            )
        )
    human_review_when = _unique_nonempty_strings(
        risk_policy.get("human_review_when"), "human_review_when"
    )
    if any(IDENTIFIER.fullmatch(condition) is None for condition in human_review_when):
        raise RuleValidationError("human_review_when_identifier_invalid")

    evidence_policy = _closed_mapping(
        data.get("evidence_policy"), "evidence_policy", EVIDENCE_POLICY_FIELDS
    )
    for field in ("direct_evidence_required", "quote_hash_required"):
        if not isinstance(evidence_policy.get(field), bool):
            raise RuleValidationError(f"{field}_invalid")
    absence_policy = _closed_mapping(
        evidence_policy.get("absence_evidence"), "absence_evidence", ABSENCE_EVIDENCE_FIELDS
    )
    absence_scope_fields = _unique_nonempty_strings(
        absence_policy.get("required_fields"), "absence_required_fields"
    )
    if not set(absence_scope_fields).issubset(ABSENCE_SCOPE_FIELDS):
        raise RuleValidationError("absence_required_field_not_allowlisted")
    missing_absence_fields = ABSENCE_SCOPE_FIELDS.difference(absence_scope_fields)
    if missing_absence_fields:
        raise RuleValidationError(
            f"absence_required_fields_incomplete:{','.join(sorted(missing_absence_fields))}"
        )
    compiled_evidence_policy = CompiledEvidencePolicy(
        direct_evidence_required=evidence_policy["direct_evidence_required"],
        quote_hash_required=evidence_policy["quote_hash_required"],
        absence_required_fields=absence_scope_fields,
    )
    invalidation_dependencies = _invalidation_dependencies(
        data.get("invalidation_dependencies"), "invalidation_dependencies"
    )
    eval_suite_refs = _evaluation_suite_references(data.get("eval_suite_refs"))
    raw_slots = data.get("slots")
    if not isinstance(raw_slots, list) or not raw_slots:
        raise RuleValidationError("slots_required")
    slots: dict[str, CompiledSlot] = {}
    for raw_slot in raw_slots:
        slot = _mapping(raw_slot, "slot")
        if unknown := slot.keys() - SLOT_FIELDS:
            raise RuleValidationError(f"unknown_slot_fields:{','.join(sorted(unknown))}")
        slot_id = slot.get("slot_id")
        if not isinstance(slot_id, str) or not IDENTIFIER.fullmatch(slot_id):
            raise RuleValidationError("slot_id_invalid")
        if slot_id in slots:
            raise RuleValidationError("duplicate_slot_id")
        if (
            not isinstance(slot.get("title"), str)
            or not slot["title"].strip()
            or not isinstance(slot.get("slot_type"), str)
            or slot["slot_type"] not in {"evidence", "fact", "judgment"}
        ):
            raise RuleValidationError("slot_metadata_invalid")
        required_when = slot.get("required_when")
        _validate_expression(required_when)
        schema = _mapping(slot.get("value_schema"), "value_schema")
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise RuleValidationError("value_schema_invalid") from exc
        evidence = _closed_mapping(
            slot.get("evidence_policy"),
            "slot_evidence_policy",
            frozenset({"kinds", "quote_required"}),
        )
        kinds = _unique_nonempty_strings(evidence.get("kinds"), "evidence_kinds")
        if not set(kinds).issubset(ALLOWED_EVIDENCE_KINDS):
            raise RuleValidationError("evidence_kind_not_allowlisted")
        if slot.get("critical") is True and not kinds:
            raise RuleValidationError("critical_slot_requires_evidence")
        if not isinstance(slot.get("required"), bool) or not isinstance(slot.get("critical"), bool):
            raise RuleValidationError("slot_required_flags_invalid")
        if not isinstance(evidence.get("quote_required"), bool):
            raise RuleValidationError("quote_required_flag_invalid")
        slot_tools = _unique_nonempty_strings(slot.get("allowed_tools"), "slot_allowed_tools")
        if not set(slot_tools).issubset(tools):
            raise RuleValidationError("slot_tool_not_in_rule_allowlist")
        states = _unique_nonempty_strings(slot.get("allowed_terminal_states"), "terminal_states")
        if not states or not set(states).issubset(ALLOWED_STATES):
            raise RuleValidationError("terminal_state_invalid")
        slot_dependencies = _unique_nonempty_strings(slot.get("dependencies"), "slot_dependencies")
        accepted_fact_types = _unique_nonempty_strings(
            slot.get("accepted_fact_types"), "accepted_fact_types"
        )
        if any(IDENTIFIER.fullmatch(fact_type) is None for fact_type in accepted_fact_types):
            raise RuleValidationError("accepted_fact_type_identifier_invalid")
        if accepted_fact_types and slot["slot_type"] != "fact":
            raise RuleValidationError("accepted_fact_types_require_fact_slot")
        validation_rules = _unique_nonempty_strings(
            slot.get("validation_rules"), "slot_validation_rules"
        )
        if not set(validation_rules).issubset(ALLOWED_SLOT_VALIDATION_RULES):
            raise RuleValidationError("slot_validation_rule_not_allowlisted")
        slot_invalidation_dependencies = _invalidation_dependencies(
            slot.get("invalidation_dependencies"), "slot_invalidation_dependencies"
        )
        slots[slot_id] = CompiledSlot(
            slot_id=slot_id,
            slot_type=slot["slot_type"],
            dependencies=slot_dependencies,
            invalidation_dependencies=slot_invalidation_dependencies,
            accepted_fact_types=accepted_fact_types,
            required=slot["required"],
            required_when=freeze_json(required_when),
            critical=slot.get("critical") is True,
            value_schema=freeze_json(schema),
            evidence_kinds=kinds,
            quote_required=evidence.get("quote_required") is True,
            validation_rules=validation_rules,
            allowed_tools=slot_tools,
            allowed_terminal_states=states,
        )
    if set(dependent_facts).intersection(slots):
        raise RuleValidationError("fact_slot_identifier_collision")
    for semantic_task, slot_id in SEMANTIC_TASK_SLOT_REQUIREMENTS.items():
        if (semantic_task in semantic_tasks) != (slot_id in slots):
            raise RuleValidationError(f"semantic_task_slot_contract_mismatch:{semantic_task}")
    slot_ids = frozenset(slots)
    slot_schemas = {slot_id: slot.value_schema for slot_id, slot in slots.items()}
    unresolved_slot_path = False
    for compiled_slot in slots.values():
        unresolved_slot_path |= _has_unresolved_slot_path(
            compiled_slot.required_when,
            facts=frozenset(dependent_facts),
            slot_schemas=slot_schemas,
        )
        _validate_expression_references(
            compiled_slot.required_when,
            facts=frozenset(dependent_facts),
            slot_ids=slot_ids,
            field=f"{compiled_slot.slot_id}_required_when",
        )
    for risk_rule in risk_rules:
        unresolved_slot_path |= _has_unresolved_slot_path(
            risk_rule["when"], facts=frozenset(dependent_facts), slot_schemas=slot_schemas
        )
        _validate_expression_references(
            risk_rule["when"],
            facts=frozenset(dependent_facts),
            slot_ids=slot_ids,
            field="minimum_judgment_rule",
        )
    blockers = [
        field
        for field in (
            "knowledge_domain",
            "basis_kind",
            "applicable_time_basis",
            "review_owner_role",
        )
        if not data.get(field)
    ]
    if not normative_refs:
        blockers.append("normative_refs_missing")
    if not enterprise_position_refs:
        blockers.append("enterprise_position_refs_missing")
    blockers.extend(
        f"dependent_check_completion_proof_unimplemented:{check_id}"
        for check_id in dependent_checks
    )
    blockers.extend(
        f"applicability_dimension_empty:{fact_name}"
        for fact_name, allowed_values in applicability_dimensions
        if not allowed_values
    )
    if unresolved_slot_path:
        blockers.append("expression_path_semantics_unresolved")
    blockers.extend(
        f"invalidation_dependency_unimplemented:{dependency}"
        for dependency in invalidation_dependencies
    )
    blockers.extend(
        f"slot_invalidation_dependency_unimplemented:{slot_id}:{dependency}"
        for slot_id in sorted(slots)
        for dependency in slots[slot_id].invalidation_dependencies
    )
    blockers.extend(
        f"slot_fact_type_missing:{slot_id}"
        for slot_id in sorted(slots)
        if slots[slot_id].slot_type == "fact" and not slots[slot_id].accepted_fact_types
    )
    blockers.extend(
        f"slot_type_unimplemented:{slot_id}:{slots[slot_id].slot_type}"
        for slot_id in sorted(slots)
        if slots[slot_id].slot_type != "fact"
    )
    blockers.extend(
        f"slot_quote_requirement_unimplemented:{slot_id}"
        for slot_id in sorted(slots)
        if not slots[slot_id].quote_required
    )
    if compiled_evidence_policy.direct_evidence_required:
        blockers.append("direct_evidence_support_unimplemented")
    blockers.extend(
        f"human_review_condition_unimplemented:{condition}" for condition in human_review_when
    )
    if any("human_confirmation" in slot.evidence_kinds for slot in slots.values()):
        blockers.append("human_confirmation_evidence_unimplemented")
    for compiled_slot in slots.values():
        for validation_rule in compiled_slot.validation_rules:
            if validation_rule not in IMPLEMENTED_SLOT_VALIDATION_RULES:
                blockers.append(f"slot_validation_rule_unimplemented:{validation_rule}")
    if set(deterministic_steps) - IMPLEMENTED_DETERMINISTIC_STEPS:
        blockers.append("deterministic_step_unimplemented")
    if set(semantic_tasks) - IMPLEMENTED_SEMANTIC_TASKS:
        blockers.append("semantic_task_unimplemented")
    blockers.extend(
        f"rule_tool_unimplemented:{tool_id}"
        for tool_id in tools
        if tool_id not in IMPLEMENTED_TOOLS
    )
    blockers.append("execution_budget_unimplemented:token_budget")
    blockers.extend(
        (
            "rule_not_published",
            "knowledge_source_content_unresolved",
            "approved_evaluation_missing",
            "publication_approval_missing",
            "rollback_target_missing",
        )
    )
    return CompiledRule(
        rule_id=rule_id,
        version=version,
        knowledge_domain=metadata_values["knowledge_domain"],
        basis_kind=metadata_values["basis_kind"],
        applicable_time_basis=metadata_values["applicable_time_basis"],
        review_owner_role=metadata_values["review_owner_role"],
        source_sha256=sha256(source).hexdigest(),
        normative_refs=normative_refs,
        enterprise_position_refs=enterprise_position_refs,
        invalidation_dependencies=invalidation_dependencies,
        applicability_dimensions=tuple(applicability_dimensions),
        applicability_expression=freeze_json(expression),
        evidence_policy=compiled_evidence_policy,
        dependent_checks=dependent_checks,
        slots=_sort_slots(slots),
        minimum_judgment_rules=tuple(compiled_risk_rules),
        human_review_when=human_review_when,
        eval_suite_refs=eval_suite_refs,
        tool_allowlist=tools,
        max_rounds=execution["max_rounds"],
        timeout_seconds=execution["timeout_seconds"],
        token_budget=execution["token_budget"],
        publication_blockers=tuple(blockers),
    )


def _versioned_reference_list(value: Any, field: str, *, namespace: str) -> tuple[str, ...]:
    references = _string_list(value, field)
    if len(references) != len(set(references)):
        raise RuleValidationError(f"{field}_duplicate")
    if any(not reference.startswith(namespace) for reference in references):
        raise RuleValidationError("knowledge_reference_category_mismatch")
    if any(
        "/" not in reference
        or not reference.split("/", maxsplit=1)[0]
        or not REFERENCE_VERSION.search(reference.rsplit("/", maxsplit=1)[-1])
        for reference in references
    ):
        raise RuleValidationError(f"{field}_must_be_versioned")
    return references


def _evaluation_suite_references(value: Any) -> tuple[str, ...]:
    references = _unique_nonempty_strings(value, "eval_suite_refs")
    for reference in references:
        suite_id, separator, version = reference.rpartition("@")
        if (
            not separator
            or IDENTIFIER.fullmatch(suite_id) is None
            or SEMVER.fullmatch(version) is None
        ):
            raise RuleValidationError("evaluation_suite_reference_invalid")
    return references


def _invalidation_dependencies(value: Any, field: str) -> tuple[str, ...]:
    dependencies = _unique_nonempty_strings(value, field)
    if any(IDENTIFIER.fullmatch(dependency) is None for dependency in dependencies):
        raise RuleValidationError(f"{field}_identifier_invalid")
    return dependencies
