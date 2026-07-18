"""Runners for text-answer tasks that produce ``solution.txt`` artifacts."""

from __future__ import annotations

from asyncio import Semaphore
import json
from pathlib import Path
import shutil

from adapter.base_adapter import BaseAdapter
from monitor.base_monitor import BaseMonitor
from utils.common import dumps, read_json_file
from utils.logging import logger


def _build_text_answer_idea(question: str, *, context_filename: str | None = None) -> str:
    context_hint = ""
    if context_filename:
        context_hint = (
            f"The context information is provided in a file named `{context_filename}` "
            "in your workspace.\n"
        )
    return (
        question
        + "I wish you finish the task with a multi-agent cooperation "
        + context_hint
        + "You SHOULD **create a file** to store your answer. The file name of your solution MUST be **'solution.txt'** and MUST be located at root directory\n"
        + "`solution.txt` should contain your solution and your final answer should be inside \\boxed{{}}"
        + """you can use the following commands which can help you complete this task.
            - Editor.create_file(filename: str)
    - Editor.insert_content_at_line(file_name: str, line_number: int, insert_content: str)
    - Editor.edit_file_by_replace(file_name: str,
        first_replaced_line_number: int,
        first_replaced_line_content: str,
        last_replaced_line_number: int,
        last_replaced_line_content: str,
        new_content: str)
    - Editor.open_file(path: str)
            """
    )


async def _run_text_answer_task(
    task: dict,
    workspace: Path,
    output: Path,
    backend: BaseAdapter,
    recovery_dir: Path = None,
    skip_existing: bool = True,
    monitor: BaseMonitor = None,
    semaphore: Semaphore = None,
    *,
    context_text: str | None = None,
    context_filename: str | None = None,
):
    async def _run() -> bool:
        data_source = task["data_source"]
        task_id = task["task_id"]
        idea = _build_text_answer_idea(task["question"], context_filename=context_filename)
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
        if context_text is not None and context_filename is not None:
            context_path = workspace / context_filename
            with open(context_path, "w", encoding="utf-8") as f:
                f.write(str(context_text))
        try:
            await backend.run_backend(
                idea=idea,
                workspace=workspace,
                recovery=recovery_dir,
                monitor=monitor,
            )
        except Exception as exc:
            logger.error(f"Error running task {data_source}/{task_id}: {exc}")

        logger.info(f"Task {data_source}/{task_id} ends executing...")
        solution_path = workspace / "solution.txt"
        if solution_path.exists():
            model_prediction = solution_path.read_text()
        else:
            model_prediction = ""
            logger.warning(f"solution.txt not found for task {data_source}/{task_id}")

        prompt_map = backend.get_prompt_map()
        history = monitor.history if monitor is not None else []
        topology = monitor.topology if monitor is not None else {}
        used_roles = {h.name for h in history}
        system_prompts = {name: prompt_map[name] for name in used_roles}

        with open(log, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "question": task["question"],
                    "question_ID": task["task_id"],
                    "ground_truth": task["reference_solution"],
                    "test": "",
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

    if semaphore is None:
        return await _run()
    async with semaphore:
        return await _run()


async def run_text_answer_task(
    task: dict,
    workspace: Path,
    output: Path,
    backend: BaseAdapter,
    recovery_dir: Path = None,
    skip_existing: bool = True,
    monitor: BaseMonitor = None,
    semaphore: Semaphore = None,
    *,
    context_text: str | None = None,
    context_filename: str | None = None,
):
    """Execute one text-answer task and persist structured execution logs."""

    return await _run_text_answer_task(
        task,
        workspace,
        output,
        backend,
        recovery_dir=recovery_dir,
        skip_existing=skip_existing,
        monitor=monitor,
        semaphore=semaphore,
        context_text=context_text,
        context_filename=context_filename,
    )
