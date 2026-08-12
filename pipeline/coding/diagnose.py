"""Diagnosis-analysis stage for proposing root-cause fix suggestions."""

from asyncio import Semaphore
from pathlib import Path
import shutil

from adapter.base_adapter import BaseAdapter
from utils.common import (
    extract_json_object_from_model_output,
    read_json_file,
    run_llm_completion,
    validate_attribution_info,
    write_json_file,
)
from utils.fault_library import fault_candidates_for_prompt
from utils.prompts import get_diagnose_analysis_prompt
from utils.logging import logger

async def diagnose_analysis(
    task: dict,
    workspace: Path,
    output: Path,
    backend: BaseAdapter,
    injection_history: list[dict] = [],
    skipping_exists: bool = True,
    message: str = None,
    semaphore: Semaphore = None,
    diagnose_mode: str = "default",
):
    """
    Generate and persist one diagnosis suggestion for a task round.

    Args:
        task: Evaluated task log containing question/history/prediction fields.
        workspace: Working directory used by backend generation.
        output: Output directory where analysis artifacts are stored.
        backend: Backend adapter used to execute prompt-driven generation.
        injection_history: Existing attack/diagnose history from prior rounds.
        skipping_exists: Whether to skip when target output already exists.
        diagnose_mode: Prompt mode — ``default`` (direct) or ``critic`` (tool-interactive CRITIC).

    Returns:
        ``True`` when a valid diagnosis analysis is generated, otherwise ``False``.
    """
    task_id = task["question_ID"]
    logger.info(
        f"Diagnose Analysis start for Task ID: {task_id}, "
        f"workspace: {workspace}, output: {output}"
    )
    min_step_id = injection_history[-1]["step_id"] if injection_history else 0
    log = output / "diagnose_analysis.json"
    if log.exists() and skipping_exists:
        logger.info(f"Log for task {task_id} exists, skipping this round...")
        try:
            validate_attribution_info(task, read_json_file(log))
        except ValueError as e:
            logger.error(f"Existing diagnose analysis invalid for task {task_id}: {e}")
            return False
        return True

    result_path = workspace / f"{task_id}_diagnose_analysis.json"
    prompt_template = get_diagnose_analysis_prompt(diagnose_mode)
    allowed_step_ids = [
        item.get("step")
        for item in task.get("history", [])
        if isinstance(item, dict) and isinstance(item.get("step"), int)
    ]
    idea = prompt_template.format(
        task_id=task["question_ID"],
        question=task["question"],
        ground_truth=task["ground_truth"],
        model_prediction=task["model_prediction"],
        fault_pool_json=fault_candidates_for_prompt(),
        topology_info=task["topology"],
        history_str=task["history"],
        injection_history=injection_history,
        allowed_step_ids=allowed_step_ids,
        min_step_id=min_step_id,
        max_step_id=len(task["history"]),
        message=message,
        workspace=result_path,
    )

    if log.exists():
        logger.info(f"Log for task {task_id} exists, overriding...")
        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(output, ignore_errors=True)
        workspace.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)

    async def _run_backend() -> object | None:
        backend_result = None
        try:
            backend_result = await run_llm_completion(idea)
        except Exception as e:
            logger.error(f"Error running task {task_id}: {e}")
            return None
        return backend_result

    if semaphore is None:
        backend_result = await _run_backend()
    else:
        async with semaphore:
            backend_result = await _run_backend()
    if backend_result is None:
        return False

    logger.info(f"Task {task_id} ends executing...")
    if not result_path.exists():
        diagnose_suggestion = extract_json_object_from_model_output(
            backend_result,
            required_keys={
                "step_id",
                "fault_code",
                "suggested_fix",
                "mistake_reason",
                "related_error",
            },
        )
        if diagnose_suggestion is None:
            logger.error(f"diagnose analysis result not found for task {task_id}")
            return False
        write_json_file(result_path, diagnose_suggestion)
        logger.warning(
            "Recovered missing diagnose analysis file from backend return for task %s -> %s",
            task_id,
            result_path,
        )
    else:
        try:
            with open(result_path, "r+", encoding="utf-8") as f:
                result = f.read().replace("\n", " ")
                first = result.index("{")
                last = result.rindex("}")
                result = result[first:last + 1]
                f.seek(0)
                f.truncate()
                f.write(result)
        except Exception:
            logger.error(f"diagnose analysis result modify errors for task {task_id}")
            return False

        try:
            diagnose_suggestion = read_json_file(result_path)
        except Exception:
            logger.error(f"diagnose analysis result read errors for task {task_id}")
            return False

    if isinstance(diagnose_suggestion, list):
        diagnose_suggestion = diagnose_suggestion[0]
    if "step_id" not in diagnose_suggestion:
        return False

    diagnose_step = diagnose_suggestion["step_id"]
    if diagnose_step <= min_step_id or diagnose_step > len(task["history"]):
        return False

    diagnose_history = injection_history + [diagnose_suggestion]
    try:
        validate_attribution_info(task, diagnose_history)
    except ValueError as e:
        logger.error(f'Diagnose analysis invalid for task {task_id}: {e}')
        return False

    write_json_file(log, diagnose_history)
    return True

def get_diagnose_analysis(
    output: Path
) -> list[dict]:
    """Load persisted diagnosis analysis history from output directory."""
    return read_json_file(output / 'diagnose_analysis.json')
