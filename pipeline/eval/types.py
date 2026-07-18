"""Shared result types for round-level evaluation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvalOutcome:
    """Normalized per-task evaluation result."""

    task_id: str
    passed: bool
    message: str = ""


@dataclass(frozen=True)
class EvalBatchResult:
    """Round-level evaluation artifacts consumed by orchestration."""

    status: dict[str, bool]
    messages: dict[str, str]


def build_batch_result(outcomes: list[EvalOutcome]) -> EvalBatchResult:
    """Convert per-task outcomes into persisted round-level maps."""

    return EvalBatchResult(
        status={outcome.task_id: outcome.passed for outcome in outcomes},
        messages={outcome.task_id: outcome.message for outcome in outcomes},
    )
