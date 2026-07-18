"""Sandbox-backed evaluators for code-generation tasks."""

from __future__ import annotations

import asyncio
import base64
from typing import Any

from pipeline.eval.types import EvalOutcome
from utils.logging import logger


def _build_request(code: str, test: str):
    from sandbox_fusion import RunCodeRequest

    base64_content = base64.b64encode(code.encode("utf-8")).decode("utf-8")
    return RunCodeRequest(
        compile_timeout=60,
        run_timeout=60,
        language="pytest",
        code=test,
        files={"solution.py": base64_content},
    )


def code_exec(code: str, test: str):
    """Execute candidate solution with provided tests in sandbox runtime."""

    try:
        from sandbox_fusion import run_code

        return run_code(_build_request(code, test), client_timeout=60)
    except Exception as exc:
        logger.error("Error in code_exec: %s", exc, exc_info=True)
        return None


async def code_exec_async(code: str, test: str):
    """Async version of :func:`code_exec`."""

    try:
        from sandbox_fusion import run_code_async

        return await run_code_async(_build_request(code, test), client_timeout=60)
    except Exception as exc:
        logger.error("Error in code_exec_async: %s", exc, exc_info=True)
        return None


def _result_stdout(result: Any) -> str:
    run_result = getattr(result, "run_result", None)
    if run_result is None:
        return ""
    return str(getattr(run_result, "stdout", "") or "")


async def evaluate_task(
    task: dict,
    *,
    semaphore: asyncio.Semaphore | None = None,
) -> EvalOutcome:
    """Run pytest-style sandbox evaluation for one task log."""

    async def _run() -> EvalOutcome:
        from sandbox_fusion import RunStatus

        task_id = task["question_ID"]
        result = await code_exec_async(task.get("model_prediction", ""), task.get("test", ""))
        if result is None:
            logger.error("Code execution failed for task %s", task_id)
            return EvalOutcome(task_id=task_id, passed=False, message="Code execution error")
        passed = result.status == RunStatus.Success
        message = _result_stdout(result)
        logger.info("Eval result for task %s: %s, message: %s", task_id, passed, str(result))
        return EvalOutcome(task_id=task_id, passed=passed, message=message)

    if semaphore is None:
        return await _run()
    async with semaphore:
        return await _run()
