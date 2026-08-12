"""Boxed-answer scorers for math datasets."""

from __future__ import annotations

import asyncio

from pipeline.eval.types import EvalOutcome
from pipeline.math.eval_equiv import math_eval


async def evaluate_task(
    task: dict,
    *,
    semaphore: asyncio.Semaphore | None = None,
) -> EvalOutcome:
    """Evaluate one math task by comparing final ``\\boxed{}`` answers."""

    async def _run() -> EvalOutcome:
        passed = math_eval(
            task.get("model_prediction", ""),
            task.get("ground_truth", ""),
        )
        return EvalOutcome(task_id=task["question_ID"], passed=passed)

    if semaphore is None:
        return await _run()
    async with semaphore:
        return await _run()
