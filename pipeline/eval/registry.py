"""Registry for choosing the right evaluator per backend and dataset."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from pipeline.eval.scorers import llm_judge, rule_based, sandbox, semantic
from pipeline.eval.types import EvalOutcome


EvaluatorCallable = Callable[..., Awaitable[EvalOutcome]]


@dataclass(frozen=True)
class EvaluatorSpec:
    """Descriptor for one evaluation strategy."""

    strategy: str
    evaluate_task: EvaluatorCallable


DEFAULT_STRATEGY = "llm_judge"
MAGENTIC_BACKEND = "MagenticOne"
DATA_SOURCE_STRATEGIES = {
    "kodcode": "sandbox",
    "gaia": "rule_based",
    "hotpotqa": "rule_based",
    "browsecomp": "llm_judge",
    "assistantbench": "llm_judge",
}
REGISTRY = {
    "sandbox": EvaluatorSpec(strategy="sandbox", evaluate_task=sandbox.evaluate_task),
    "rule_based": EvaluatorSpec(strategy="rule_based", evaluate_task=rule_based.evaluate_task),
    "llm_judge": EvaluatorSpec(strategy="llm_judge", evaluate_task=llm_judge.evaluate_task),
    "universal": EvaluatorSpec(strategy="universal", evaluate_task=semantic.evaluate_task),
}


def resolve_strategy(backend: str | None, data_source: str) -> str:
    """Resolve evaluator strategy from backend and dataset metadata."""

    if backend == MAGENTIC_BACKEND:
        return "universal"
    return DATA_SOURCE_STRATEGIES.get(data_source, DEFAULT_STRATEGY)


def resolve_evaluator(backend: str | None, data_source: str) -> EvaluatorSpec:
    """Return the evaluator descriptor for one round."""

    strategy = resolve_strategy(backend, data_source)
    return REGISTRY[strategy]
