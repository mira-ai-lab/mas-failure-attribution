"""MATH evaluation with official-style LaTeX equivalence on \\boxed{} answers."""

from __future__ import annotations

from pipeline.math.equivalence import is_equiv, strip_string
from pipeline.math.eval import extract_boxed_answer, normalize_boxed_answer
from utils.logging import logger


def math_eval(solution: str, ground_truth: str) -> bool:
    """Return whether prediction and reference agree on the final boxed answer."""

    try:
        truth = normalize_boxed_answer(extract_boxed_answer(ground_truth))
        if not truth:
            return False
        answer = normalize_boxed_answer(extract_boxed_answer(solution))
        return is_equiv(strip_string(answer), strip_string(truth))
    except Exception as exc:
        logger.error("Error in math_eval: %s", exc, exc_info=True)
        return False
