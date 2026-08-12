"""Attack-analysis stage for generating new fault injection suggestions."""

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
from utils.prompts import ATTACK_ANALYSIS_PROMPT
from utils.logging import logger
from utils.expert_group_report import (
    filter_injectable_step_ids,
    format_injectable_steps_for_prompt,
    validate_attack_suggestion,
)


def _injectable_steps_after_min(task: dict, min_step_id: int) -> list[int]:
    """Return injectable step ids strictly greater than ``min_step_id``."""
    history = task.get("history", [])
    return [step for step in filter_injectable_step_ids(history) if step > min_step_id]


async def attack_analysis(
    task: dict,
    workspace: Path,
    output: Path,
    backend: BaseAdapter,
    injection_history: list[dict] = [],
    skipping_exists: bool = True,
    message: str = None,
    semaphore: Semaphore = None,
):
    """
    Generate and persist one attack suggestion for a task round.

    Args:
        task: Evaluated task log containing question/history/prediction fields.
        workspace: Working directory used by backend generation.
        output: Output directory where analysis artifacts are stored.
        backend: Backend adapter used to execute prompt-driven generation.
        injection_history: Existing attack/diagnose history from prior rounds.
        skipping_exists: Whether to skip when target output already exists.

    Returns:
        ``True`` when a valid attack analysis is generated, otherwise ``False``.
    """
    task_id = task["question_ID"]
    log = output / "attack_analysis.json"

    if log.exists() and skipping_exists:
        logger.info(f"Log for task {task_id} exists, skipping this round...")
        try:
            existing = read_json_file(log)
            validate_attribution_info(task, existing)
            for item in existing:
                validate_attack_suggestion(task, item)
        except ValueError as e:
            logger.error(f"Existing attack analysis invalid for task {task_id}: {e}")
            return False
        return True

    async def _run_backend():
        logger.info(f"Attack Analysis start for Task ID: {task_id}")
        if len(injection_history) > 0:
            min_step_id = injection_history[-1]["step_id"]
        else:
            min_step_id = 0

        allowed_step_ids = _injectable_steps_after_min(task, min_step_id)
        if not allowed_step_ids:
            logger.error(
                "No injectable attack steps for task %s after min_step_id=%s",
                task_id,
                min_step_id,
            )
            return None

        injectable_step_agents = format_injectable_steps_for_prompt(
            task.get("history", []),
            allowed_step_ids,
        )
        result_path = workspace / f"{task_id}_attack_analysis.json"
        idea = ATTACK_ANALYSIS_PROMPT.format(
            task_id=task["question_ID"],
            question=task["question"],
            ground_truth=task["ground_truth"],
            model_prediction=task["model_prediction"],
            fault_pool_json=fault_candidates_for_prompt(),
            topology_info=task["topology"],
            history_str=task["history"],
            injection_history=injection_history,
            allowed_step_ids=allowed_step_ids,
            injectable_step_agents=injectable_step_agents,
            min_step_id=min_step_id,
            max_step_id=len(task["history"]),
            message=message,
            workspace=result_path,
        )

        if log.exists():
            if skipping_exists:
                logger.info(f"Log for task {task_id} exists, skipping this round...")
                return read_json_file(log)
            logger.info(f"Log for task {task_id} exists, overriding...")
            shutil.rmtree(workspace, ignore_errors=True)
            shutil.rmtree(output, ignore_errors=True)
            workspace.mkdir(parents=True, exist_ok=True)
            output.mkdir(parents=True, exist_ok=True)

        backend_result = None
        try:
            backend_result = await run_llm_completion(idea)
        except Exception as e:
            logger.error(f"Error running task {task_id}: {e}")
            return None

        logger.info(f"Task {task_id} ends executing...")
        if result_path.exists():
            try:
                with open(result_path, "r+", encoding="utf-8") as f:
                    result = f.read().replace("\n", " ")
                    first = result.index("{")
                    last = result.index("}")
                    result = result[first:last + 1]
                    f.seek(0)
                    f.truncate()
                    f.write(result)
            except Exception:
                logger.error(f"attack analysis result modify errors for task {task_id}")
                return None

            try:
                attack_suggestion = read_json_file(result_path)
            except Exception:
                logger.error(f"attack analysis result read errors for task {task_id}")
                return None
        else:
            attack_suggestion = extract_json_object_from_model_output(
                backend_result,
                required_keys={
                    "step_id",
                    "fault_code",
                    "attacked_content",
                    "mistake_reason",
                    "related_error",
                },
            )
            if attack_suggestion is None:
                logger.error(f"attack analysis result not found for task {task_id}")
                return None
            write_json_file(result_path, attack_suggestion)
            logger.warning(
                "Recovered missing attack analysis file from backend return for task %s -> %s",
                task_id,
                result_path,
            )

        attacked_step = attack_suggestion["step_id"]
        if attacked_step <= min_step_id or attacked_step > len(task["history"]):
            logger.error(
                "Attack step out of range for task %s: step=%s min=%s max=%s",
                task_id,
                attacked_step,
                min_step_id,
                len(task["history"]),
            )
            return None
        if attacked_step not in allowed_step_ids:
            logger.error(
                "Attack step not injectable for task %s: step=%s allowed=%s",
                task_id,
                attacked_step,
                allowed_step_ids,
            )
            return None
        try:
            validate_attack_suggestion(task, attack_suggestion)
        except ValueError as e:
            logger.error("Attack suggestion rejected for task %s: %s", task_id, e)
            return None
        return attack_suggestion

    if semaphore is None:
        attack_suggestion = await _run_backend()
    else:
        async with semaphore:
            attack_suggestion = await _run_backend()

    if not attack_suggestion:
        return False

    attack_history = injection_history + [attack_suggestion]
    try:
        validate_attribution_info(task, attack_history)
        validate_attack_suggestion(task, attack_suggestion)
    except ValueError as e:
        logger.error(f"Attack analysis invalid for task {task_id}: {e}")
        return False

    write_json_file(log, attack_history)
    return True


def get_attack_analysis(output: Path) -> list[dict]:
    """Load persisted attack analysis history from output directory."""
    return read_json_file(output / "attack_analysis.json")
