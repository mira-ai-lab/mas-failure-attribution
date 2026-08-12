"""Runners for code-generation tasks that produce ``solution.py`` artifacts."""

from __future__ import annotations

from asyncio import Semaphore
import json
from pathlib import Path
import shutil

from adapter.base_adapter import BaseAdapter
from monitor.base_monitor import BaseMonitor
from utils.common import dumps, read_json_file
from utils.logging import logger


def _magentic_one_code_generation_idea(task: dict, workspace: Path) -> str:
    """Build a workspace-bound MagenticOne prompt for code-generation tasks."""

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


def _default_code_generation_idea(task: dict) -> str:
    # signature = str(task.get("reference_solution", "")).split("\n")[0]
    return (
        task["question"]
        + "I wish you finish the task with a multi-agent cooperation"
        + "The file name of your solution MUST be 'solution.py' and MUST be located at root directory"
        # + f"The signature of function is {signature}"

    )


async def run_code_generation_task(
    task: dict,
    workspace: Path,
    output: Path,
    backend: BaseAdapter,
    recovery_dir: Path = None,
    skip_existing: bool = True,
    monitor: BaseMonitor = None,
    semaphore: Semaphore = None,
):
    """Execute one code-generation task and persist structured execution logs."""

    async def _execute_one_task() -> bool:
        data_source = task["data_source"]
        task_id = task["task_id"]
        if type(backend).__name__ == "MagenticOneAdapter":
            idea = _magentic_one_code_generation_idea(task, workspace)
        else:
            idea = _default_code_generation_idea(task)
        log = output / "log.json"
        if log.exists():
            log_content = read_json_file(log)
            if log_content["history"] == []:
                shutil.rmtree(output, ignore_errors=True)
                output.mkdir(parents=True, exist_ok=True)
            elif skip_existing:
                logger.info(f"Log for task {task_id} exists, skipping this round...")
                return True
            else:
                logger.info(f"Log for task {task_id} exists, overriding...")
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
        except Exception as exc:
            logger.error(f"Error running task {data_source}/{task_id}: {exc}")

        logger.info(f"Task {data_source}/{task_id} ends executing...")
        model_prediction = ""
        solution_path = workspace / "solution.py"
        if solution_path.exists():
            model_prediction = solution_path.read_text()
        else:
            logger.warning(f"solution.py not found for task {data_source}/{task_id}")

        prompt_map = backend.get_prompt_map()
        history = monitor.history if monitor is not None else []
        topology = monitor.topology if monitor is not None else {}
        used_roles = {h.name for h in history}
        topology_nodes = set(getattr(topology, "nodes", []) or [])
        visible_roles = used_roles | topology_nodes
        system_prompts = {name: prompt_map[name] for name in visible_roles if name in prompt_map}

        with open(log, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "question": task["question"],
                    "question_ID": task["task_id"],
                    "ground_truth": task["reference_solution"],
                    "test": task["test"],
                    "model_prediction": model_prediction,
                    "history": dumps(history),
                    "topology": dumps(topology),
                    "system_prompts": system_prompts,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        return True

    if semaphore is not None:
        async with semaphore:
            return await _execute_one_task()
    return await _execute_one_task()
