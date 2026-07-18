"""Rule-based scorers for exact-match style datasets."""

from __future__ import annotations

import asyncio
import re
import string

from pipeline.eval.types import EvalOutcome


def _is_float(element) -> bool:
    try:
        float(element)
        return True
    except (TypeError, ValueError):
        return False


def _normalize_number_str(number_str: str) -> float:
    for char in ["$", "%", ","]:
        number_str = number_str.replace(char, "")
    try:
        return float(number_str)
    except ValueError:
        return float("inf")


def _normalize_str(input_str, remove_punct: bool = True) -> str:
    if input_str is None:
        return ""
    # Match the official GAIA scorer by removing all whitespace, not just
    # trimming leading/trailing spaces.
    text = re.sub(r"\s", "", str(input_str)).lower()
    if remove_punct:
        trans_table = str.maketrans("", "", string.punctuation)
        text = text.translate(trans_table)
    return text


def _split_string(value: str, char_list: list[str] | None = None) -> list[str]:
    import re

    if char_list is None:
        char_list = [",", ";"]
    pattern = f"[{''.join(char_list)}]"
    return re.split(pattern, value)


def gaia_score(model_answer: str, ground_truth: str) -> bool:
    """GAIA scorer aligned with the official benchmark implementation."""

    # Based on the official GAIA scorer:
    # https://huggingface.co/spaces/gaia-benchmark/leaderboard/blob/main/scorer.py
    model_answer = "None" if model_answer is None else str(model_answer)
    ground_truth = "" if ground_truth is None else str(ground_truth)
    if _is_float(ground_truth):
        return _normalize_number_str(model_answer) == float(ground_truth)
    if any(char in ground_truth for char in [",", ";"]):
        gt_elems = _split_string(ground_truth)
        ma_elems = _split_string(model_answer)
        if len(gt_elems) != len(ma_elems):
            return False
        comparisons = []
        for ma_elem, gt_elem in zip(ma_elems, gt_elems):
            if _is_float(gt_elem):
                comparisons.append(_normalize_number_str(ma_elem) == float(gt_elem))
            else:
                comparisons.append(
                    _normalize_str(ma_elem, remove_punct=False)
                    == _normalize_str(gt_elem, remove_punct=False)
                )
        return all(comparisons)
    return _normalize_str(model_answer) == _normalize_str(ground_truth)


async def evaluate_task(
    task: dict,
    *,
    semaphore: asyncio.Semaphore | None = None,
) -> EvalOutcome:
    """Evaluate one task using GAIA-style normalized exact matching."""

    async def _run() -> EvalOutcome:
        return EvalOutcome(
            task_id=task["question_ID"],
            passed=gaia_score(
                task.get("model_prediction", ""),
                task.get("ground_truth", ""),
            ),
        )

    if semaphore is None:
        return await _run()
    async with semaphore:
        return await _run()
