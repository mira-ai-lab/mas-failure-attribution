"""Universal evaluator used by the MagenticOne backend."""

from __future__ import annotations

import asyncio
import json

from pipeline.eval.scorers.sandbox import code_exec_async
from pipeline.eval.types import EvalOutcome
from utils.logging import logger


async def llm_semantic_eval(prediction: str, ground_truth: str, question: str) -> bool:
    """Use the MagenticOne model client to compare answers semantically."""

    if not prediction or not ground_truth:
        return False
    if prediction.strip().lower() == ground_truth.strip().lower():
        return True

    system_prompt = """You are a precise evaluator. Your task is to determine if the 'Model Prediction' correctly answers the 'Question' by comparing it with the 'Ground Truth'.

Rules:
1. The prediction does not need to be word-for-word identical to the ground truth.
2. If the prediction conveys the same meaning, facts, or numerical value as the ground truth, mark it as correct.
3. If the prediction is irrelevant, factually wrong, or contradicts the ground truth, mark it as incorrect.
4. Return ONLY a JSON object with a single key "is_correct" (boolean). Do not add any explanation.

Example Output: {"is_correct": true}
"""

    user_prompt = f"""
Question: {question}
Ground Truth: {ground_truth}
Model Prediction: {prediction}
"""

    try:
        from adapter.MagenticOne.core import _build_model_client
        from autogen_core.models import SystemMessage, UserMessage

        client = _build_model_client()
        result = await client.create(
            [
                SystemMessage(content=system_prompt),
                UserMessage(content=user_prompt, source="user"),
            ],
        )
        text = result.content
        if not isinstance(text, str):
            raise TypeError(f"Expected string completion, got {type(text)}")
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            if len(lines) >= 2 and lines[-1].strip() == "```":
                text = "\n".join(lines[1:-1]).strip()
        if "{" in text and "}" in text:
            start, end = text.find("{"), text.rfind("}")
            if end > start:
                text = text[start : end + 1]
        result_json = json.loads(text)
        return bool(result_json.get("is_correct", False))
    except Exception as exc:
        logger.warning("LLM eval failed for question: %s. Falling back to strict mismatch.", exc)
        return False


async def evaluate_task(
    task: dict,
    *,
    semaphore: asyncio.Semaphore | None = None,
) -> EvalOutcome:
    """Evaluate one MagenticOne task by test presence or semantic comparison."""

    async def _run() -> EvalOutcome:
        task_id = task["question_ID"]
        prediction = task.get("model_prediction", "")
        test_code = task.get("test", "")
        if test_code and test_code.strip():
            from sandbox_fusion import RunStatus

            result = await code_exec_async(prediction, test_code)
            if result is None:
                return EvalOutcome(task_id=task_id, passed=False, message="Code execution error")
            passed = getattr(result, "status", None) == RunStatus.Success
            message = str(getattr(getattr(result, "run_result", None), "stdout", "") or "")
            return EvalOutcome(task_id=task_id, passed=passed, message=message)
        passed = await llm_semantic_eval(
            prediction,
            task.get("ground_truth", ""),
            task.get("question", ""),
        )
        return EvalOutcome(task_id=task_id, passed=passed)

    if semaphore is None:
        return await _run()
    async with semaphore:
        return await _run()
