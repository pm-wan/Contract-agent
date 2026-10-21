from dataclasses import replace
from pathlib import Path

import pytest

from contract_app.rules.compiler import load_development_rule
from contract_app.rules.dependency_graph import (
    MAX_RULE_DEFINITIONS,
    MAX_RULE_SOURCE_BYTES,
    CheckDefinition,
    CheckRegistryInvalid,
    build_check_execution_graph,
    build_rule_execution_graph,
    load_rule_execution_graph,
    validate_check_execution_graph_manifest,
)

RULE_PATH = (
    Path(__file__).parents[1] / "docs/development/06-rules/rules/legal.payment_conditions.v1.yaml"
)


def test_check_execution_graph_orders_explicit_dependencies_deterministically() -> None:
    base = load_development_rule(RULE_PATH)
    downstream = replace(base, dependent_checks=("check.price",))
    upstream = replace(base, dependent_checks=())

    graph = build_check_execution_graph(
        (
            CheckDefinition("check.payment", downstream),
            CheckDefinition("check.price", upstream),
        )
    )

    assert graph.status == "graph_resolved"
    assert graph.execution_order == ("check.price", "check.payment")
    assert graph.blockers == ()
    assert len(graph.graph_sha256) == 64


def test_check_execution_graph_blocks_missing_definitions_and_cycles() -> None:
    base = load_development_rule(RULE_PATH)
    missing = build_check_execution_graph(
        (CheckDefinition("check.payment", replace(base, dependent_checks=("check.price",))),)
    )
    assert missing.status == "blocked"
    assert missing.blockers == ("dependent_check_definition_missing:check.price",)
    assert missing.execution_order == ()

    first = CheckDefinition("check.first", replace(base, dependent_checks=("check.second",)))
    second = CheckDefinition("check.second", replace(base, dependent_checks=("check.first",)))
    cyclic = build_check_execution_graph((first, second))
    assert cyclic.status == "blocked"
    assert cyclic.blockers == (
        "check_dependency_cycle:check.first",
        "check_dependency_cycle:check.second",
    )
    assert cyclic.execution_order == ()


def test_check_execution_graph_rejects_duplicate_check_ids() -> None:
    base = load_development_rule(RULE_PATH)
    definition = CheckDefinition("check.payment", replace(base, dependent_checks=()))

    with pytest.raises(CheckRegistryInvalid, match="check_definition_duplicate"):
        build_check_execution_graph((definition, definition))


def test_graph_manifest_validator_rejects_more_than_the_rule_catalog_limit() -> None:
    base = load_development_rule(RULE_PATH)
    definitions = tuple(
        CheckDefinition(f"rule-{index:02}", base) for index in range(MAX_RULE_DEFINITIONS + 1)
    )
    manifest = build_check_execution_graph(definitions).as_manifest()

    with pytest.raises(CheckRegistryInvalid, match="check_graph_manifest_invalid"):
        validate_check_execution_graph_manifest(manifest)


def test_rule_execution_graph_exposes_the_missing_first_rule_dependency() -> None:
    payment_rule = load_development_rule(RULE_PATH)

    graph = build_rule_execution_graph((payment_rule,))

    assert graph.status == "blocked"
    assert graph.blockers == ("dependent_check_definition_missing:legal.price.definition",)
    assert graph.execution_order == ()


def test_directory_graph_fails_closed_for_missing_rule_file(tmp_path: Path) -> None:
    (tmp_path / "legal.payment_conditions.v1.yaml").write_bytes(RULE_PATH.read_bytes())

    graph = load_rule_execution_graph(tmp_path)

    assert graph.status == "blocked"
    assert graph.blockers == ("dependent_check_definition_missing:legal.price.definition",)
    assert graph.execution_order == ()


def test_directory_graph_orders_registered_rule_definitions(tmp_path: Path) -> None:
    payment_source = RULE_PATH.read_text(encoding="utf-8")
    (tmp_path / "payment.yaml").write_text(payment_source, encoding="utf-8")
    price_source = payment_source.replace(
        "rule_id: legal.payment_conditions", "rule_id: legal.price.definition", 1
    ).replace("  checks:\n    - legal.price.definition", "  checks: []", 1)
    (tmp_path / "price.yaml").write_text(price_source, encoding="utf-8")

    graph = load_rule_execution_graph(tmp_path)

    assert graph.status == "graph_resolved"
    assert graph.blockers == ()
    assert graph.execution_order == (
        "legal.price.definition",
        "legal.payment_conditions",
    )


def test_directory_graph_rejects_too_many_rule_files(tmp_path: Path) -> None:
    for index in range(65):
        (tmp_path / f"rule-{index:02}.yaml").write_bytes(b"not parsed before count limit")

    with pytest.raises(CheckRegistryInvalid, match="rule_registry_too_large"):
        load_rule_execution_graph(tmp_path)


def test_directory_graph_rejects_an_oversized_rule_source(tmp_path: Path) -> None:
    source = tmp_path / "oversized.yaml"
    source.write_bytes(b"x" * (MAX_RULE_SOURCE_BYTES + 1))

    with pytest.raises(CheckRegistryInvalid, match="rule_registry_source_too_large"):
        load_rule_execution_graph(tmp_path)


def test_directory_graph_rejects_excessive_total_source_bytes(tmp_path: Path) -> None:
    base = RULE_PATH.read_bytes()
    padding = b"#" + b"x" * (240 * 1024)
    for index in range(17):
        (tmp_path / f"rule-{index:02}.yaml").write_bytes(base + padding)

    with pytest.raises(CheckRegistryInvalid, match="rule_registry_too_large"):
        load_rule_execution_graph(tmp_path)


def test_directory_graph_rejects_symlinked_rule_source(tmp_path: Path) -> None:
    source = tmp_path / "real.yaml"
    source.write_bytes(RULE_PATH.read_bytes())
    (tmp_path / "linked.yaml").symlink_to(source)

    with pytest.raises(CheckRegistryInvalid, match="rule_registry_source_invalid"):
        load_rule_execution_graph(tmp_path)
