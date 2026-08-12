"""Core coding-task execution and replay helpers for each round."""

from asyncio import Semaphore
from enum import Enum
import json
from pathlib import Path
import shutil
from typing import Type

from tqdm.asyncio import tqdm

from adapter.base_adapter import BaseAdapter
from model.schema import History
from monitor.attack_monitor import AttackMonitor
from monitor.base_monitor import BaseMonitor
from utils.common import dumps, read_json_file
from utils.logging import logger

class RunMode(Enum):
    """Execution mode hints for task running strategies."""

    NONE = 0
    ATTACK = 1
    DIAGNOSE = 2
def _magentic_one_task_idea(task: dict, workspace: Path) -> str:
    """MagenticOne user message: kodcode gets workspace-bound ``solution.py`` instructions; others question only."""
    if task.get("data_source") != "kodcode":
        return task["question"]
    workspace_dir = workspace.resolve()
    solution_full = workspace_dir / "solution.py"
    return (
        f"{task['question']}\n\n"
        "Finish this task using multi-agent cooperation.\n\n"
        "Output requirements:\n"
        "- Create or overwrite a file named exactly `solution.py`.\n"
        "- Write it to exactly this path (do not use the repository root or another CWD):\n"
        f"  {solution_full}\n"
        "- Perform FileSurfer / Python file writes for the final solution under this directory:\n"
        f"  {workspace_dir}\n"
    )

async def run_coding_task(
    task: dict,
    workspace: Path,
    output: Path,
    backend: BaseAdapter,
    recovery_dir: Path = None,
    skip_existing: bool = True,
    monitor: BaseMonitor = None,
    semaphore: Semaphore = None,
):
    """
    Execute one coding task and persist structured execution logs.

    Args:
        task: Dataset task record.
        workspace: Workspace directory for backend execution.
        output: Output directory for logs and artifacts.
        backend: Backend adapter implementing run/serialize APIs.
        recovery_dir: Optional recovery directory for backend resume.
        skip_existing: Whether to skip when ``log.json`` already exists.
        monitor: Execution monitor used to capture history and topology.
        replay_info: Prompt prefix used during replayed runs.

    Returns:
        ``True`` when run completes and logs are saved, otherwise ``False``.
    """
    async with semaphore:
        data_source = task["data_source"]
        task_id = task["task_id"]
        #signature = task["reference_solution"].split("\n")[0]
        if type(backend).__name__ == "MagenticOneAdapter":
            idea = _magentic_one_task_idea(task, workspace)
        else:
            idea = (
                task["question"]
                + f"I wish you finish the task with a multi-agent cooperation"
                + f"The file name of your solution MUST be 'solution.py' and MUST be located at root directory"
                + f'The signature of function is {signature}'
            )
        log = output / 'log.json'
        if log.exists():
            log_content = read_json_file(log)
            if log_content['history'] == []:
                shutil.rmtree(output, ignore_errors=True)
                output.mkdir(parents=True, exist_ok=True)
            elif skip_existing:
                logger.info(f'Log for task {task_id} exists, skipping this round...')
                return True
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
                recovery=recovery_dir,
                monitor=monitor,
                task_id=task_id,
            )
        except Exception as e:
            logger.error(f"Error running task {data_source}/{task_id}: {e}")
        
        logger.info(f'Task {data_source}/{task_id} ends executing...')
        if data_source == "kodcode":
            solution_path = workspace / 'solution.py'
            if solution_path.exists():
                model_prediction = solution_path.read_text()
            else:
                model_prediction = ""
                logger.warning(f'solution.py not found for task {data_source}/{task_id}')
        else:
            tr_getter = getattr(backend, "get_task_result_prediction_for_log", None)
            if callable(tr_getter):
                model_prediction = tr_getter() or ""
                if not model_prediction.strip():
                    logger.warning(
                        f"No TaskResult-derived prediction for task {data_source}/{task_id} "
                        f"(log model_prediction left empty)."
                    )
        prompt_map = backend.get_prompt_map()

        used_roles = set(h.name for h in monitor.history)
        system_prompts = {
            name: prompt_map[name]
            for name in used_roles
        }

        with open(log, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "question": task["question"],
                    "question_ID": task["task_id"],
                    "ground_truth": task["reference_solution"],
                    "test": task["test"],
                    "model_prediction": model_prediction,
                    "history": dumps(monitor.history),
                    "topology": dumps(monitor.topology),
                    "system_prompts": system_prompts,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    return True