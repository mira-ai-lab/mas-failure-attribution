"""Math task evaluation via \\boxed{} answer extraction."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from tqdm.asyncio import tqdm

from utils.common import read_json_file
from utils.logging import logger

_BOXED_PREFIX = r"\boxed{"
_OUTER_BRACE_RE = re.compile(r"^\{([^{}]+)\}$")
_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_MAX_OUTER_BRACE_PEELS = 2


def extract_boxed_answer(text: str) -> str:
    """Extract content of the last ``\\boxed{...}`` using balanced brace matching."""

    if not text:
        return ""
    start = text.rfind(_BOXED_PREFIX)
    if start == -1:
        return ""
    i = start + len(_BOXED_PREFIX)
    depth = 1
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + len(_BOXED_PREFIX) : i].strip()
        i += 1
    return ""


def normalize_boxed_answer(raw: str) -> str:
    """Normalize extracted boxed content for stable equality checks."""

    if not raw:
        return ""
    value = raw.strip()
    for _ in range(_MAX_OUTER_BRACE_PEELS):
        match = _OUTER_BRACE_RE.match(value)
        if not match:
            break
        value = match.group(1).strip()
    if _NUMERIC_RE.fullmatch(value):
        try:
            number = float(value)
        except (ValueError, OverflowError):
            return value
        if abs(number - round(number)) < 1e-9:
            return str(int(round(number)))
    return value


def math_eval(solution: str, ground_truth: str) -> bool:
    """Return whether prediction and reference agree on the final boxed answer."""

    try:
        truth = normalize_boxed_answer(extract_boxed_answer(ground_truth))
        if not truth:
            return False
        answer = normalize_boxed_answer(extract_boxed_answer(solution))
        return answer == truth
    except Exception as exc:
        logger.error("Error in math_eval: %s", exc, exc_info=True)
        return False


async def run_correctness_eval_task(
    task: dict,
    semaphore: asyncio.Semaphore | None = None,
) -> tuple[str, bool, str]:
    """Run one task evaluation and return ``(task_id, passed, message)``."""

    async def _run() -> tuple[str, bool, str]:
        result = math_eval(task["model_prediction"], task["ground_truth"])
        logger.info("Eval result for task %s: %s", task["question_ID"], result)
        return task["question_ID"], result, ""

    if semaphore is None:
        return await _run()
    async with semaphore:
        return await _run()


async def run_eval_tasks(
    eval_path: Path,
    data_source: str,
    semaphore: asyncio.Semaphore | None = None,
    skip_existing: bool = True,
) -> dict[str, bool] | None:
    """Evaluate all task logs under one round directory and save pass/fail map."""

    logger.info("Starting to eval tasks in %s...", eval_path)
    filename = f"eval_{data_source}.json"
    save_path = eval_path / filename
    msg_path = eval_path / f"eval_msg_{data_source}.json"
    if save_path.exists():
        if skip_existing:
            logger.info("Eval result for %s exists, skipping...", data_source)
            return None
        logger.info("Eval result for %s exists, overriding...", data_source)

    eval_log_paths = [p / "log.json" for p in eval_path.iterdir() if p.is_dir()]
    tasks = [read_json_file(p) for p in eval_log_paths if p.exists()]
    results = await tqdm.gather(*(run_correctness_eval_task(task, semaphore) for task in tasks))
    status = {task_id: result for task_id, result, _ in results}
    messages = {task_id: message for task_id, _, message in results}
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(status, f)
    with open(msg_path, "w", encoding="utf-8") as f:
        json.dump(messages, f)
    logger.info("Ending to eval tasks in %s, results saved in %s...", eval_path, save_path)
    return status


def load_eval_results(
    eval_path: Path,
    data_source: str,
) -> tuple[dict[str, bool], dict[str, str]]:
    """Load previously saved evaluation result mapping for one round."""

    eval_results = read_json_file(eval_path / f"eval_{data_source}.json")
    msg_results = read_json_file(eval_path / f"eval_msg_{data_source}.json")
    return eval_results, msg_results
