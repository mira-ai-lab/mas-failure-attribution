"""Runners for web-research tasks that produce ``final_answer.txt`` artifacts."""

from __future__ import annotations

import inspect
import json
import re
from asyncio import Semaphore
from pathlib import Path
import shutil

from adapter.base_adapter import BaseAdapter
from monitor.base_monitor import BaseMonitor
from utils.common import dumps
from utils.expert_group_report import CAPTAIN_FRAMEWORK_ROLES, COMPUTER_TERMINAL
from utils.logging import logger


BROWSECOMP_RESEARCH_INSTRUCTIONS = """

BrowseComp research constraints:
- This is a multi-hop web research task. Do not rely on search snippets as final evidence.
- Identify the exact entity or value being asked for before selecting a final answer.
- Collect candidate answers only from retrieved page content, not from snippets alone.
- Before finalizing, check whether the retrieved evidence contains conflicting candidates or ambiguous entity matches.
- If sources conflict or the evidence is indirect, continue searching with changed entities, quoted phrases, date clues, location clues, or source types.
- If a page extraction fails because of robots, access, rate limits, timeout, or empty content, do not treat that source as evidence. Try a different source.
- Prefer directly relevant primary or biography/reference pages over generic list pages, search-result pages, and pages that only mention the entity in passing.
- The final Exact Answer must contain only the requested entity/value, and it must be directly supported by retrieved page content during this run.
""".strip()


ASSISTANTBENCH_RESEARCH_INSTRUCTIONS = """

AssistantBench execution protocol:
- Treat this as a precision web-research task, not a general web-summary task.
- First parse the hard constraints in the question: entity type, location/radius, dates/times, price/rating/count thresholds, output format, and whether multiple answers are expected.
- Build a candidate table before finalizing. For each candidate record: name, source URL, source type, evidence text, every constraint checked, pass/fail, and rejection reason.
- Use retrieved page content as evidence. Search snippets may suggest candidates but must not be final evidence.
- Prefer official pages, public schedules, booking pages, government databases, exchange/product pages, or task-specific authoritative sources. Generic blog/list pages are candidate sources only, not final proof unless the task explicitly asks for such pages.
- For location/radius/walking constraints, verify the exact branch/location, not just the brand. Reject citywide chains or remote branches unless the exact address satisfies the constraint.
- For schedule/time constraints, verify event/class/departure/sale time, not just opening hours. "Open before X" does not prove "has a class before X". If the question says "before 7am", 7:00am is not before 7am.
- For price/rating/review/count constraints, verify the numeric value and threshold from a retrieved source. Do not round or infer unless the task asks for it.
- For comparison/ranking tasks, collect all named candidates, compute the requested metric explicitly, and choose the best after comparison.
- If a page extraction fails, try the official site, a booking page, an alternate URL, or a narrower quoted query. Do not treat failed extraction as evidence.
- Before producing the final answer, state a compact verification table in the analysis. The final_answer must contain only candidates that passed every hard constraint.
- If the answer has multiple items, put one item per line unless the question asks for JSON or another explicit format.
""".strip()


WEB_RESEARCH_OUTPUT_CONTRACT = """

Output requirements:
- Use the backend's multi-agent web-research workflow to investigate the question.
- Write the final concise answer to `final_answer.txt` in the workspace root.
- `final_answer.txt` must contain only the final answer, with no label, markdown, or extra commentary.
""".strip()


def _assistantbench_task_prompt(question: str) -> str:
    """Return an AssistantBench-specific prompt with a fixed research protocol."""

    return (
        f"{question}\n\n"
        f"{ASSISTANTBENCH_RESEARCH_INSTRUCTIONS}\n\n"
        "Required working method:\n"
        "1. Restate the hard constraints as a checklist.\n"
        "2. Search for candidates using at least two query styles: broad candidate discovery and targeted official/schedule/source lookup.\n"
        "3. Verify each candidate against every checklist item using retrieved page content.\n"
        "4. Reject candidates with missing evidence, wrong location, wrong time, wrong price/rating/count, or only generic brand evidence.\n"
        "5. Produce <analysis> with the candidate verification table and then <final_answer> with only the final answer.\n"
    )


def _build_web_research_idea(task: dict) -> str:
    data_source = task["data_source"]
    file_hint = ""
    if task.get("file_name"):
        file_hint = f"\nNecessary file: {task['file_name']}"
    base_question = task["question"] + file_hint
    if data_source == "browsecomp":
        task_prompt = f"{base_question}\n\n{BROWSECOMP_RESEARCH_INSTRUCTIONS}"
    elif data_source == "assistantbench":
        task_prompt = _assistantbench_task_prompt(base_question)
    else:
        task_prompt = base_question
    return f"{task_prompt}\n\n{WEB_RESEARCH_OUTPUT_CONTRACT}"


_FINAL_ANSWER_PATTERNS = (
    re.compile(r"(?is)final\s*answer\s*:\s*(.+)$"),
    re.compile(r"(?is)<final_answer>\s*(.+?)\s*</final_answer>"),
)


def _history_entry_field(entry: object, field: str) -> str:
    value = getattr(entry, field, None)
    if value is None and isinstance(entry, dict):
        value = entry.get(field)
    return "" if value is None else str(value)


def _normalize_web_research_answer(text: str) -> str:
    """Normalize a candidate web-research answer into concise plain text."""

    cleaned = (text or "").strip()
    if not cleaned:
        return ""

    for pattern in _FINAL_ANSWER_PATTERNS:
        match = pattern.search(cleaned)
        if match:
            cleaned = match.group(1).strip()
            break

    cleaned = cleaned.strip("`").strip()
    if cleaned.lower().startswith("final answer:"):
        cleaned = cleaned.split(":", 1)[1].strip()

    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return ""
    if len(lines) == 1:
        return lines[0]

    # Prefer the last non-empty line when the model also emitted analysis text.
    return lines[-1]


def _extract_text_from_result(result: object, backend: BaseAdapter) -> str:
    """Best-effort extraction of text from a backend return payload."""

    def _backend_fallback() -> str:
        getter = getattr(backend, "get_task_result_prediction_for_log", None)
        if callable(getter):
            return (getter() or "").strip()
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
            if isinstance(content, str) and content.strip():
                return content.strip()

    text = str(result).strip()
    return text if text else _backend_fallback()


def _find_answer_in_history(history: list) -> str:
    """Find the latest concise answer candidate in monitor history."""

    if not history:
        return ""

    summoner_entries = [
        entry
        for entry in history
        if _history_entry_field(entry, "name") == "Expert_summoner"
        or "Response from seek_agent_help" in _history_entry_field(entry, "content")
    ]
    for entry in reversed(summoner_entries):
        answer = _normalize_web_research_answer(_history_entry_field(entry, "content"))
        if answer:
            return answer

    expert_entries = [
        entry
        for entry in history
        if _history_entry_field(entry, "name") not in CAPTAIN_FRAMEWORK_ROLES
        and _history_entry_field(entry, "name") != COMPUTER_TERMINAL
    ]
    for entry in reversed(expert_entries):
        answer = _normalize_web_research_answer(_history_entry_field(entry, "content"))
        if answer:
            return answer

    for entry in reversed(history):
        answer = _normalize_web_research_answer(_history_entry_field(entry, "content"))
        if answer:
            return answer
    return ""


def _extract_web_research_prediction(
    result: object,
    backend: BaseAdapter,
    history: list,
) -> str:
    """Extract a concise final answer when ``final_answer.txt`` was not written."""

    for candidate in (
        _extract_text_from_result(result, backend),
        _find_answer_in_history(history),
    ):
        answer = _normalize_web_research_answer(candidate)
        if answer:
            return answer
    return ""


def _resolve_model_prediction(
    workspace: Path,
    *,
    result: object,
    backend: BaseAdapter,
    history: list,
    data_source: str,
    task_id: str,
) -> str:
    """Read ``final_answer.txt`` or backfill from backend output/history."""

    answer_path = workspace / "final_answer.txt"
    if answer_path.exists():
        return answer_path.read_text(encoding="utf-8").strip()

    model_prediction = _extract_web_research_prediction(result, backend, history)
    if not model_prediction:
        logger.warning(
            "final_answer.txt not found and no prediction extracted for %s/%s",
            data_source,
            task_id,
        )
        return ""

    answer_path.write_text(model_prediction, encoding="utf-8")
    logger.info(
        "Backfilled final_answer.txt for %s/%s from backend output/history",
        data_source,
        task_id,
    )
    return model_prediction


async def run_web_research_task(
    task: dict,
    workspace: Path,
    output: Path,
    backend: BaseAdapter,
    recovery_dir: Path = None,
    skip_existing: bool = True,
    monitor: BaseMonitor = None,
    semaphore: Semaphore = None,
):
    """Execute one web-research task and persist structured execution logs."""

    async def _run() -> bool:
        data_source = task["data_source"]
        task_id = task["task_id"]
        idea = _build_web_research_idea(task)
        log = output / "log.json"
        if log.exists():
            if skip_existing:
                logger.info(f"Log for task {task_id} exists, skipping this round...")
                return True
            logger.info(f"Log for task {task_id} exists, overriding...")
            shutil.rmtree(workspace, ignore_errors=True)
            shutil.rmtree(output, ignore_errors=True)
            workspace.mkdir(parents=True, exist_ok=True)
            output.mkdir(parents=True, exist_ok=True)

        workspace.mkdir(parents=True, exist_ok=True)
        output.mkdir(parents=True, exist_ok=True)
        backend_result = None
        try:
            backend_result = backend.run_backend(
                idea=idea,
                workspace=workspace,
                recovery=recovery_dir,
                monitor=monitor,
                task_id=task_id,
            )
            if inspect.isawaitable(backend_result):
                backend_result = await backend_result
        except Exception as exc:
            logger.error(f"Error running web-research task {data_source}/{task_id}: {exc}")
            return False

        logger.info(f"Task {data_source}/{task_id} ends executing...")
        history = monitor.history if monitor is not None else []
        model_prediction = _resolve_model_prediction(
            workspace,
            result=backend_result,
            backend=backend,
            history=history,
            data_source=data_source,
            task_id=task_id,
        )
        if not model_prediction:
            return False

        with open(log, "w", encoding="utf-8") as f:
            prompt_map = backend.get_prompt_map()
            history = monitor.history if monitor is not None else []
            topology = monitor.topology if monitor is not None else {}
            used_roles = {h.name for h in history}
            topology_nodes = set(getattr(topology, "nodes", []) or [])
            visible_roles = used_roles | topology_nodes
            system_prompts = {name: prompt_map[name] for name in visible_roles if name in prompt_map}
            json.dump(
                {
                    "question": task["question"],
                    "question_ID": task["task_id"],
                    "ground_truth": task["reference_solution"],
                    "test": task.get("test", ""),
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
