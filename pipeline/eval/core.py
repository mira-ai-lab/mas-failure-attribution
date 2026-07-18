"""Unified round evaluation entrypoints."""

from __future__ import annotations

import asyncio
from pathlib import Path

from tqdm.asyncio import tqdm

from pipeline.eval.io import eval_result_path, load_eval_artifacts, load_round_tasks, write_eval_artifacts
from pipeline.eval.registry import resolve_evaluator
from pipeline.eval.types import build_batch_result
from utils.logging import logger


async def evaluate_round(
    eval_path: Path,
    data_source: str,
    *,
    backend: str | None = None,
    semaphore: asyncio.Semaphore | None = None,
    skip_existing: bool = True,
) -> tuple[dict[str, bool], dict[str, str]]:
    """Evaluate all task logs under one round directory."""

    logger.info("Starting unified eval for %s in %s...", data_source, eval_path)
    save_path = eval_result_path(eval_path, data_source)
    if save_path.exists() and skip_existing:
        logger.info("Eval result for %s exists, loading cached files...", data_source)
        cached = load_eval_artifacts(eval_path, data_source)
        return cached.status, cached.messages
    if save_path.exists():
        logger.info("Eval result for %s exists, overriding...", data_source)

    tasks = load_round_tasks(eval_path)
    evaluator = resolve_evaluator(backend, data_source)
    logger.info("Using %s evaluator for backend=%s data_source=%s", evaluator.strategy, backend, data_source)
    outcomes = await tqdm.gather(
        *(evaluator.evaluate_task(task, semaphore=semaphore) for task in tasks)
    ) if tasks else []
    result = build_batch_result(outcomes)
    write_eval_artifacts(eval_path, data_source, result)
    logger.info("Unified eval finished for %s, results saved to %s", data_source, save_path)
    return result.status, result.messages


def load_eval_results(eval_path: Path, data_source: str) -> tuple[dict[str, bool], dict[str, str]]:
    """Load persisted evaluation artifacts for one round."""

    result = load_eval_artifacts(eval_path, data_source)
    return result.status, result.messages
