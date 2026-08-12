"""Compatibility wrappers for the unified MagenticOne evaluation path."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pipeline.eval.core import evaluate_round, load_eval_results
from pipeline.eval.scorers.semantic import llm_semantic_eval
from pipeline.eval.scorers.sandbox import code_exec


async def _run_universal_eval_task(task: dict):
    from pipeline.eval.scorers.semantic import evaluate_task

    return await evaluate_task(task)


def run_universal_eval_task(task: dict) -> tuple[str, bool]:
    """Compatibility helper that keeps the legacy synchronous surface."""

    result = asyncio.run(_run_universal_eval_task(task))
    return result.task_id, result.passed


def run_eval_tasks_new(
    eval_path: Path,
    data_source: str,
    skip_existing: bool = True,
):
    """Compatibility wrapper delegating to the unified evaluator."""

    status, _ = asyncio.run(
        evaluate_round(
            eval_path,
            data_source,
            backend="MagenticOne",
            semaphore=None,
            skip_existing=skip_existing,
        )
    )
    return status


def load_eval_results_new(eval_path: Path, data_source: str):
    """Load legacy MagenticOne eval results from the unified artifacts."""

    status, _ = load_eval_results(eval_path, data_source)
    return status
