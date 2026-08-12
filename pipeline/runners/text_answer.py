"""Runners for text-answer tasks that produce ``solution.txt`` artifacts."""

from __future__ import annotations

from asyncio import Semaphore
import json
from pathlib import Path
import shutil

from adapter.base_adapter import BaseAdapter
from adapter.Captain.core import CaptainAdapter
from monitor.base_monitor import BaseMonitor
from pipeline.math.eval import extract_boxed_answer
from utils.expert_group_report import (
    CAPTAIN_FRAMEWORK_ROLES,
    COMPUTER_TERMINAL,
    build_expert_group_summary,
    log_expert_group_summary,
    update_expert_group_report,
)
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
        + "Return your final answer directly in plain text. "
        + "Put the final value inside \\boxed{{}} in your final response."
    )


def _extract_text_prediction(result: object, backend: BaseAdapter) -> str:
    """Best-effort extraction of a text answer from backend return payload."""

    def _backend_fallback() -> str:
        tr_getter = getattr(backend, "get_task_result_prediction_for_log", None)
        if callable(tr_getter):
            return (tr_getter() or "").strip()
        return ""

    if result is None:
        return _backend_fallback()

    if isinstance(result, str):
        text = result.strip()
        return text if text else _backend_fallback()

    summary = getattr(result, "summary", None)
    if isinstance(summary, str) and summary.strip():
        return summary.strip()

    chat_history = getattr(result, "chat_history", None)
    if isinstance(chat_history, list) and chat_history:
        last_msg = chat_history[-1]
        if isinstance(last_msg, dict):
            content = last_msg.get("content")
            if isinstance(content, str):
                text = content.strip()
                if text:
                    return text

    text = str(result).strip()
    return text if text else _backend_fallback()


def _history_entry_field(entry: object, field: str) -> str:
    value = getattr(entry, field, None)
    if value is None and isinstance(entry, dict):
        value = entry.get(field)
    return "" if value is None else str(value)


def _boxed_from_history_entries(entries: list) -> str | None:
    for entry in reversed(entries):
        content = _history_entry_field(entry, "content")
        if not content.strip():
            continue
        answer = extract_boxed_answer(content)
        if answer:
            return answer
    return None


def _find_boxed_in_history(history: list) -> str | None:
    """Find the latest non-empty ``\\boxed{}`` answer in monitor history."""
    if not history:
        return None

    summoner_entries = [
        entry
        for entry in history
        if _history_entry_field(entry, "name") == "Expert_summoner"
        or "Response from seek_agent_help" in _history_entry_field(entry, "content")
    ]
    answer = _boxed_from_history_entries(summoner_entries)
    if answer:
        return answer

    expert_entries = [
        entry
        for entry in history
        if _history_entry_field(entry, "name") not in CAPTAIN_FRAMEWORK_ROLES
        and _history_entry_field(entry, "name") != COMPUTER_TERMINAL
    ]
    return _boxed_from_history_entries(expert_entries)


def _resolve_model_prediction_for_math(
    summary: str,
    history: list,
    *,
    data_source: str,
) -> str:
    """Ensure math logs expose a final ``\\boxed{}`` answer for strict eval."""
    if data_source != "math":
        return summary

    summary = summary or ""
    if extract_boxed_answer(summary):
        return summary

    boxed = _find_boxed_in_history(history)
    if boxed:
        logger.info(
            "Backfilled \\boxed{} into model_prediction from history for math task"
        )
        return f"{summary.rstrip()}\n\n\\boxed{{{boxed}}}"

    if summary.strip():
        logger.warning(
            "math model_prediction has no \\boxed{} in summary or history"
        )
    return summary


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
        backend_result = None
        try:
            backend_result = await backend.run_backend(
                idea=idea,
                workspace=workspace,
                recovery=recovery_dir,
                monitor=monitor,
                task_id=task_id,
            )
        except Exception as exc:
            logger.error(f"Error running task {data_source}/{task_id}: {exc}")

        logger.info(f"Task {data_source}/{task_id} ends executing...")
        history = monitor.history if monitor is not None else []
        model_prediction = _extract_text_prediction(backend_result, backend)
        model_prediction = _resolve_model_prediction_for_math(
            model_prediction,
            history,
            data_source=data_source,
        )
        solution_path = workspace / "solution.txt"
        if model_prediction:
            solution_path.write_text(model_prediction, encoding="utf-8")
        elif solution_path.exists():
            model_prediction = solution_path.read_text(encoding="utf-8")
        else:
            logger.warning(f"solution.txt not found for task {data_source}/{task_id}")

        with open(log, "w", encoding="utf-8") as f:
            prompt_map = backend.get_prompt_map()
            topology = monitor.topology if monitor is not None else {}
            used_roles = {h.name for h in history}
            topology_nodes = set(getattr(topology, "nodes", []) or [])
            visible_roles = used_roles | topology_nodes
            system_prompts = {name: prompt_map[name] for name in visible_roles if name in prompt_map}
            log_payload = {
                "question": task["question"],
                "question_ID": task["task_id"],
                "ground_truth": task["reference_solution"],
                "test": "",
                "model_prediction": model_prediction,
                "history": dumps(history),
                "topology": dumps(topology),
                "system_prompts": system_prompts,
            }
            if isinstance(backend, CaptainAdapter):
                captain_lib_mode = (backend.last_agent_library_info or {}).get("mode")
                expert_group_summary = build_expert_group_summary(
                    task_id=task_id,
                    workspace=workspace,
                    history=history,
                    captain_lib_mode=captain_lib_mode,
                )
                log_payload["expert_group_summary"] = expert_group_summary
                report_path = update_expert_group_report(
                    output=output,
                    task_id=task_id,
                    round_snapshot=expert_group_summary,
                )
                log_expert_group_summary(expert_group_summary, report_path)
            json.dump(
                log_payload,
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
