"""Resolve rule-bound AgentScope limits with an independent worker ceiling."""

from dataclasses import dataclass

from contract_app.rules.compiler import CompiledRule


@dataclass(frozen=True, slots=True)
class ReviewExecutionBudget:
    max_rounds: int
    timeout_seconds: float


def resolve_review_execution_budget(
    rule: CompiledRule, *, worker_timeout_ceiling: float
) -> ReviewExecutionBudget:
    """Apply the stricter of the frozen rule timeout and local worker ceiling."""
    if worker_timeout_ceiling <= 0:
        raise ValueError("invalid_review_timeout_ceiling")
    return ReviewExecutionBudget(
        max_rounds=rule.max_rounds,
        timeout_seconds=min(float(rule.timeout_seconds), worker_timeout_ceiling),
    )
