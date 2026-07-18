
"""Attack-analysis stage for generating new fault injection suggestions."""

import ast
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
from utils.prompts import ATTACK_ANALYSIS_PROMPT
from utils.logging import logger

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
    log = output / 'attack_analysis.json'

    if log.exists() and skipping_exists:
        logger.info(f'Log for task {task_id} exists, skipping this round...')
        try:
            validate_attribution_info(task, read_json_file(log))
        except ValueError as e:
            logger.error(f'Existing attack analysis invalid for task {task_id}: {e}')
            return False
        return True
   
    async def _run_backend():
        logger.info(f'Attack Analysis start for Task ID: {task_id}')
        if len(injection_history) > 0:
            min_step_id = injection_history[-1]['step_id']
        else:
            min_step_id = 0
        allowed_step_ids = [
            item.get('step')
            for item in task.get('history', [])
            if isinstance(item, dict) and isinstance(item.get('step'), int)
        ]
        result_path = workspace / f'{task_id}_attack_analysis.json'
        idea = ATTACK_ANALYSIS_PROMPT.format(
            task_id=task["question_ID"],
            question=task["question"],
            ground_truth=task["ground_truth"],
            model_prediction=task["model_prediction"],
            fault_pool_json=fault_candidates_for_prompt(),
            topology_info=task['topology'],
            history_str=task['history'],
            injection_history=injection_history,
            allowed_step_ids=allowed_step_ids,
            min_step_id=min_step_id,
            max_step_id=len(task['history']),
            message=message,
            workspace=result_path,
        )

        if log.exists():
            if skipping_exists:
                logger.info(f'Log for task {task_id} exists, skipping this round...')
                return read_json_file(log)
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
            return None
        
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
                return None
            try:
                attack_suggestion = read_json_file(result_path)
            except:
                logger.error(f'attack analysis result read errors for task {task_id}')
                return None
        else:
            logger.error(f'attack analysis result not found for task {task_id}')
            return None
        
        attacked_step = attack_suggestion['step_id']
        if attacked_step <= min_step_id or attacked_step > len(task['history']):
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
    except ValueError as e:
        logger.error(f'Attack analysis invalid for task {task_id}: {e}')
        return False

    write_json_file(
        log,
        attack_history
    )
    return True

def get_attack_analysis(
    output: Path
) -> list[dict]:
    """Load persisted attack analysis history from output directory."""
    return read_json_file(output / 'attack_analysis.json')
