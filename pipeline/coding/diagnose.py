"""Diagnosis-analysis stage for proposing root-cause fix suggestions."""

from asyncio import Semaphore
import json
from pathlib import Path
import shutil
from typing import Type

from adapter.base_adapter import BaseAdapter
from utils.common import (
    read_json_file,
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
    logger.info(f'Diagnose Analysis start for Task ID: {task_id}, workspace: {workspace}, output: {output}')
    if len(injection_history) > 0:
        min_step_id = injection_history[-1]['step_id']
    else:
        min_step_id = 0
    allowed_step_ids = [
        item.get('step')
        for item in task.get('history', [])
        if isinstance(item, dict) and isinstance(item.get('step'), int)
    ]
    idea = DIAGNOSE_ANALYSIS_PROMPT.format(
        task_id=task["question_ID"],
        question=task["question"],
        ground_truth=task["ground_truth"],
        model_prediction=task["model_prediction"],
        fault_pool_json=fault_candidates_for_prompt(),
        topology_info=task['topology'],
        history_str=task['history'],
        injection_history=injection_history,
        min_step_id=min_step_id,
        allowed_step_ids=allowed_step_ids,
    )
    log = output / 'diagnose_analysis.json'
    if log.exists():
        if skipping_exists:
            logger.info(f'Log for task {task_id} exists, skipping this round...')
            try:
                validate_attribution_info(task, read_json_file(log))
            except ValueError as e:
                logger.error(f'Existing diagnose analysis invalid for task {task_id}: {e}')
                return False
            return True
    async with semaphore:
        task_id = task["question_ID"]
        logger.info(f'Diagnose Analysis start for Task ID: {task_id}, workspace: {workspace}, output: {output}')
        if len(injection_history) > 0:
            min_step_id = injection_history[-1]['step_id']
        else:
            min_step_id = 0
        result_path = workspace / f'{task_id}_diagnose_analysis.json'
        prompt_template = get_diagnose_analysis_prompt(diagnose_mode)
        idea = prompt_template.format(
            task_id=task["question_ID"],
            question=task["question"],
            ground_truth=task["ground_truth"],
            model_prediction=task["model_prediction"],
            fault_pool_json=fault_candidates_for_prompt(),
            topology_info=task['topology'],
            history_str=task['history'],
            injection_history=injection_history,
            min_step_id=min_step_id,
            max_step_id=len(task['history']),
            message=message,
            workspace=result_path,
        )
        log = output / 'diagnose_analysis.json'
        if log.exists():
            if skipping_exists:
                logger.info(f'Log for task {task_id} exists, skipping this round...')
                return
            else:
                logger.info(f'Log for task {task_id} exists, overriding...')
                shutil.rmtree(workspace, ignore_errors=True)
                shutil.rmtree(output, ignore_errors=True)
                workspace.mkdir(parents=True, exist_ok=True)
                output.mkdir(parents=True, exist_ok=True)

        try:
            await backend.run_backend(
                idea=idea,
                workspace=workspace,
                enable_lint=False,
            )
        except Exception as e:
            logger.error(f"Error running task {task_id}: {e}")
            return False
    else:
        logger.error(f'diagnose analysis result not found for task {task_id}')
        return False

    diagnose_history = injection_history + [diagnose_suggestion]
    try:
        validate_attribution_info(task, diagnose_history)
    except ValueError as e:
        logger.error(f'Diagnose analysis invalid for task {task_id}: {e}')
        return False

    write_json_file(
        log,
        diagnose_history
    )
        
        logger.info(f'Task {task_id} ends executing...')
        if result_path.exists():
            try:
                with open(result_path, 'r+') as f:
                    result = f.read()
                    result = result.replace('\n', ' ')
                    first = result.index('{')
                    last = result.index('}')
                    result = result[first:last+1]
                    f.seek(0)          # 指针移到开头
                    f.truncate()       # 清空原有内容
                    f.write(result)
            except:
                logger.error(f'attack analysis result modify errors for task {task_id}')
                return False
            try:
                diagnose_suggestion = read_json_file(result_path)
            except:
                logger.error(f'attack analysis result read errors for task {task_id}')
                return False
        else:
            logger.error(f'diagnose analysis result not found for task {task_id}')
            return False
        if isinstance(diagnose_suggestion, list):
            diagnose_suggestion = diagnose_suggestion[0]
        
        if not 'step_id' in diagnose_suggestion:
            return False
        diagnose_step = diagnose_suggestion['step_id']
        if diagnose_step <= min_step_id or diagnose_step > len(task['history']):
            return False
        
        write_json_file(
            log,
            injection_history + [diagnose_suggestion]
        )
    return True

def get_diagnose_analysis(
    output: Path
) -> list[dict]:
    """Load persisted diagnosis analysis history from output directory."""
    return read_json_file(output / 'diagnose_analysis.json')
