
"""Attack-analysis stage for generating new fault injection suggestions."""

from pathlib import Path
import shutil

from adapter.base_adapter import BaseAdapter
from utils.common import read_json_file, write_json_file
from utils.fault_library import fault_candidates_for_prompt
from utils.magentic_trace_recovery import recover_attack_analysis_from_magentic_trace
from utils.prompts import ATTACK_ANALYSIS_PROMPT
from utils.logging import logger

async def attack_analysis(
    task: dict,
    workspace: Path,
    output: Path,
    backend: BaseAdapter,
    injection_history: list[dict] = [],
    skipping_exists: bool = True,
    semaphore = None,
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
    async with semaphore:
        task_id = task["question_ID"]
        logger.info(f'Attack Analysis start for Task ID: {task_id}')
        if len(injection_history) > 0:
            min_step_id = injection_history[-1]['step_id']
        else:
            min_step_id = 0

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
            min_step_id=min_step_id,
            workspace=workspace
        )
        log = output / 'attack_analysis.json'
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
        
        logger.info(f'Task {task_id} ends executing...')
        if result_path.exists():
            try:
                attack_suggestion = read_json_file(result_path)
            except Exception:
                logger.error(f'attack analysis result read errors for task {task_id}')
                return False
        else:
            recovered = recover_attack_analysis_from_magentic_trace(workspace)
            if recovered is not None:
                logger.info(
                    f'Recovered attack JSON from magentic_trace for task {task_id} '
                    f'(agents did not write {result_path.name})'
                )
                write_json_file(result_path, recovered)
                attack_suggestion = recovered
            else:
                logger.error(f'attack analysis result not found for task {task_id}')
                return False

        write_json_file(
            log,
            injection_history + [attack_suggestion]
        )
    return True

def get_attack_analysis(
    output: Path
) -> list[dict]:
    """Load persisted attack analysis history from output directory."""
    return read_json_file(output / 'attack_analysis.json')