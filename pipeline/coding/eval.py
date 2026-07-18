"""Compatibility wrappers for the unified evaluation package."""

from __future__ import annotations

import asyncio
from pathlib import Path

from pipeline.eval.core import evaluate_round, load_eval_results
from pipeline.eval.scorers.llm_judge import BROWSECOMP_GRADER_TEMPLATE, judge_answer as _judge_browsecomp_answer
from pipeline.eval.scorers.rule_based import gaia_score
from pipeline.eval.scorers.sandbox import code_exec, code_exec_async


async def run_correctness_eval_task(
    task: dict,
    semaphore: asyncio.Semaphore | None = None,
):
    """Compatibility wrapper for legacy sandbox-style task evaluation."""

    from pipeline.eval.scorers.sandbox import evaluate_task

    result = await evaluate_task(task, semaphore=semaphore)
    return result.task_id, result.passed, result.message


def run_gaia_eval_task(task: dict) -> tuple[str, bool]:
    """Compatibility wrapper for legacy GAIA scoring."""

    return task["question_ID"], gaia_score(
        task.get("model_prediction", ""),
        task.get("ground_truth", ""),
    )


def run_browsecomp_eval_task(task: dict) -> tuple[str, bool]:
    """Compatibility wrapper for legacy BrowseComp judging."""

    return task["question_ID"], _judge_browsecomp_answer(
        task.get("question", ""),
        task.get("ground_truth", ""),
        task.get("model_prediction", ""),
    )


def run_assistantbench_eval_task(task: dict) -> tuple[str, bool]:
    """Compatibility wrapper for legacy AssistantBench judging."""

    return run_browsecomp_eval_task(task)


async def run_eval_tasks(
    eval_path: Path,
    data_source: str,
    semaphore: asyncio.Semaphore | None = None,
    skip_existing: bool = True,
) -> dict[str, bool]:
    """Compatibility wrapper delegating to the unified evaluator."""

    status, _ = await evaluate_round(
        eval_path,
        data_source,
        backend=None,
        semaphore=semaphore,
        skip_existing=skip_existing,
    )
    return status
