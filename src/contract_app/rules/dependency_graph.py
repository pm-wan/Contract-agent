"""Build deterministic development execution order from explicit check definitions.

This module establishes graph structure only. It does not authenticate knowledge,
approve rules, execute checks, or create completion proofs.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from contract_app.bounded_files import read_bounded_file
from contract_app.rules.compiler import CompiledRule, load_development_rule_bytes

MAX_RULE_SOURCE_BYTES = 256 * 1024
MAX_RULE_DEFINITIONS = 64
MAX_RULE_CATALOG_BYTES = 4 * 1024 * 1024


class CheckRegistryInvalid(ValueError):
    """An explicit check registry is malformed or ambiguous."""


@dataclass(frozen=True, slots=True)
class CheckDefinition:
    check_id: str
    rule: CompiledRule
    source_bytes: bytes | None = None


@dataclass(frozen=True, slots=True)
class CheckExecutionGraph:
    definitions: tuple[CheckDefinition, ...]
    execution_order: tuple[str, ...]
    blockers: tuple[str, ...]
    graph_sha256: str

    @property
    def status(self) -> str:
        return "blocked" if self.blockers else "graph_resolved"

    def as_manifest(self) -> dict[str, object]:
        definitions = [
            {
                "check_id": definition.check_id,
                "rule_id": definition.rule.rule_id,
                "rule_version": definition.rule.version,
                "rule_sha256": definition.rule.source_sha256,
                "dependencies": list(sorted(definition.rule.dependent_checks)),
            }
            for definition in self.definitions
        ]
        return {
            "kind": "development_rule_dependency_graph",
            "status": self.status,
            "definitions": definitions,
            "execution_order": list(self.execution_order),
            "blockers": list(self.blockers),
            "graph_sha256": self.graph_sha256,
            "is_execution_proof": False,
            "is_approval_record": False,
        }


def validate_check_execution_graph_manifest(value: object) -> dict[str, object]:
    """Validate the closed, hash-bound graph metadata stored in a run definition."""
    expected_fields = {
        "kind",
        "status",
        "definitions",
        "execution_order",
        "blockers",
        "graph_sha256",
        "is_execution_proof",
        "is_approval_record",
    }
    if not isinstance(value, dict) or value.keys() != expected_fields:
        raise CheckRegistryInvalid("check_graph_manifest_shape_invalid")
    definitions = value.get("definitions")
    blockers = value.get("blockers")
    execution_order = value.get("execution_order")
    if (
        value.get("kind") != "development_rule_dependency_graph"
        or value.get("is_execution_proof") is not False
        or value.get("is_approval_record") is not False
        or value.get("status") not in {"blocked", "graph_resolved"}
        or not isinstance(definitions, list)
        or len(definitions) > MAX_RULE_DEFINITIONS
        or not isinstance(blockers, list)
        or not isinstance(execution_order, list)
    ):
        raise CheckRegistryInvalid("check_graph_manifest_invalid")
    check_ids: list[str] = []
    for definition in definitions:
        if (
            not isinstance(definition, dict)
            or definition.keys()
            != {"check_id", "rule_id", "rule_version", "rule_sha256", "dependencies"}
            or any(
                not isinstance(definition.get(field), str) or not definition[field]
                for field in ("check_id", "rule_id", "rule_version")
            )
            or not isinstance(definition.get("rule_sha256"), str)
            or len(definition["rule_sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in definition["rule_sha256"])
            or not isinstance(definition.get("dependencies"), list)
            or any(not isinstance(item, str) or not item for item in definition["dependencies"])
            or definition["dependencies"] != sorted(set(definition["dependencies"]))
        ):
            raise CheckRegistryInvalid("check_graph_manifest_definition_invalid")
        check_ids.append(definition["check_id"])
    if check_ids != sorted(set(check_ids)):
        raise CheckRegistryInvalid("check_graph_manifest_definition_invalid")
    if (
        any(not isinstance(item, str) or not item for item in blockers)
        or blockers != sorted(set(blockers))
        or value["status"] != ("blocked" if blockers else "graph_resolved")
        or any(not isinstance(item, str) or item not in check_ids for item in execution_order)
        or len(execution_order) != len(set(execution_order))
        or (blockers and execution_order)
        or (not blockers and set(execution_order) != set(check_ids))
    ):
        raise CheckRegistryInvalid("check_graph_manifest_invalid")
    graph_body = {
        "definitions": definitions,
        "execution_order": execution_order,
        "blockers": blockers,
    }
    digest = sha256(
        json.dumps(graph_body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    if value.get("graph_sha256") != digest:
        raise CheckRegistryInvalid("check_graph_manifest_hash_mismatch")
    return value


def build_check_execution_graph(
    definitions: Sequence[CheckDefinition],
) -> CheckExecutionGraph:
    """Resolve explicit check IDs and return a stable topological ordering.

    A check ID is supplied separately from its compiled rule so this function
    does not assume that every check name is itself a RuleDefinition ID.
    """
    by_id: dict[str, CheckDefinition] = {}
    for definition in definitions:
        if (
            not isinstance(definition, CheckDefinition)
            or not isinstance(definition.check_id, str)
            or not definition.check_id.strip()
        ):
            raise CheckRegistryInvalid("check_definition_invalid")
        if definition.check_id in by_id:
            raise CheckRegistryInvalid("check_definition_duplicate")
        by_id[definition.check_id] = definition

    dependency_map = {
        check_id: tuple(sorted(definition.rule.dependent_checks))
        for check_id, definition in by_id.items()
    }
    blockers = {
        f"dependent_check_definition_missing:{dependency}"
        for dependencies in dependency_map.values()
        for dependency in dependencies
        if dependency not in by_id
    }

    dependents: dict[str, set[str]] = {check_id: set() for check_id in by_id}
    indegree = {check_id: 0 for check_id in by_id}
    for check_id, dependencies in dependency_map.items():
        for dependency in dependencies:
            if dependency in by_id:
                dependents[dependency].add(check_id)
                indegree[check_id] += 1

    ready = sorted(check_id for check_id, count in indegree.items() if count == 0)
    order: list[str] = []
    while ready:
        check_id = ready.pop(0)
        order.append(check_id)
        for dependent in sorted(dependents[check_id]):
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                ready.append(dependent)
                ready.sort()

    cyclic = sorted(check_id for check_id, count in indegree.items() if count > 0)
    blockers.update(f"check_dependency_cycle:{check_id}" for check_id in cyclic)
    execution_order = tuple(order) if not blockers else ()
    ordered_definitions = tuple(by_id[check_id] for check_id in sorted(by_id))
    blocker_tuple = tuple(sorted(blockers))
    snapshot = {
        "definitions": [
            {
                "check_id": definition.check_id,
                "rule_id": definition.rule.rule_id,
                "rule_version": definition.rule.version,
                "rule_sha256": definition.rule.source_sha256,
                "dependencies": list(dependency_map[definition.check_id]),
            }
            for definition in ordered_definitions
        ],
        "execution_order": list(execution_order),
        "blockers": list(blocker_tuple),
    }
    graph_sha256 = sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return CheckExecutionGraph(
        definitions=ordered_definitions,
        execution_order=execution_order,
        blockers=blocker_tuple,
        graph_sha256=graph_sha256,
    )


def build_rule_execution_graph(rules: Sequence[CompiledRule]) -> CheckExecutionGraph:
    """Use RuleDefinition IDs as check IDs under the current development convention.

    The convention matches the first-rule catalogue but remains a product/domain
    decision. The returned graph is never an execution or approval record.
    """
    return build_check_execution_graph(tuple(CheckDefinition(rule.rule_id, rule) for rule in rules))


def load_rule_execution_graph(rule_directory: Path) -> CheckExecutionGraph:
    """Compile direct YAML children of a rule directory and inspect their graph."""
    rule_paths = sorted(rule_directory.glob("*.yaml"))
    if not rule_paths:
        raise CheckRegistryInvalid("rule_registry_empty")
    if len(rule_paths) > MAX_RULE_DEFINITIONS:
        raise CheckRegistryInvalid("rule_registry_too_large")
    rules: list[tuple[CompiledRule, bytes]] = []
    total_source_bytes = 0
    for path in rule_paths:
        if path.is_symlink() or not path.is_file():
            raise CheckRegistryInvalid("rule_registry_source_invalid")
        try:
            source = read_bounded_file(path, MAX_RULE_SOURCE_BYTES)
        except OSError as exc:
            raise CheckRegistryInvalid("rule_registry_source_unavailable") from exc
        if len(source) > MAX_RULE_SOURCE_BYTES:
            raise CheckRegistryInvalid("rule_registry_source_too_large")
        total_source_bytes += len(source)
        if total_source_bytes > MAX_RULE_CATALOG_BYTES:
            raise CheckRegistryInvalid("rule_registry_too_large")
        try:
            rules.append((load_development_rule_bytes(source), source))
        except ValueError as exc:
            raise CheckRegistryInvalid("rule_registry_definition_invalid") from exc
    graph = build_check_execution_graph(
        tuple(CheckDefinition(rule.rule_id, rule, source) for rule, source in rules)
    )
    return graph
