"""LLM-judge scorers for web-research datasets."""

from __future__ import annotations

import asyncio
import os
import re

from pipeline.eval.types import EvalOutcome
from utils.logging import logger


JUDGE_ANSWER = """
Judge whether the following [response] to [question] is correct or not based on the precise and unambiguous [correct_answer] below.

[question]: {question}

[response]: {response}

Your judgement must be in the format and criteria specified below:

extracted_final_answer: The final exact answer extracted from the [response]. Put the extracted answer as 'None' if there is no exact, final answer to extract from the response.

[correct_answer]: {correct_answer}

reasoning: Explain why the extracted_final_answer is correct or incorrect based on [correct_answer], focusing only on if there are meaningful differences between [correct_answer] and the extracted_final_answer. Do not comment on any background to the problem, do not attempt to solve the problem, do not argue for any answer different than [correct_answer], focus only on whether the answers match.

correct: Answer 'yes' if extracted_final_answer matches the [correct_answer] given above, or is within a small margin of error for numerical problems. Answer 'no' otherwise, i.e. if there if there is any inconsistency, ambiguity, non-equivalency, or if the extracted answer is incorrect.


confidence: The extracted confidence score between 0|%| and 100|%| from [response]. Put 100 if there is no confidence score available.
""".strip()


def _load_judge_env() -> tuple[str, str, str]:
    """Load dedicated LLM-judge runtime env.

    Preferred keys:
    - ``JUDGE_API_KEY``
    - ``JUDGE_BASE_URL``
    - ``JUDGE_MODEL_NAME``

    Optional fallback:
    - ``OPENAI_API_KEY`` / ``OPENAI_BASE_URL`` / ``OPENAI_MODEL``
    """

    api_key = (os.getenv("JUDGE_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    base_url = (os.getenv("JUDGE_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").strip()
    model_name = (os.getenv("JUDGE_MODEL_NAME") or os.getenv("OPENAI_MODEL") or "").strip()

    missing: list[str] = []
    if not api_key:
        missing.append("JUDGE_API_KEY|OPENAI_API_KEY")
    if not base_url:
        missing.append("JUDGE_BASE_URL|OPENAI_BASE_URL")
    if not model_name:
        missing.append("JUDGE_MODEL_NAME|OPENAI_MODEL")
    if missing:
        raise RuntimeError(
            "Missing LLM judge configuration: " + ", ".join(missing)
        )

    return api_key, base_url, model_name


def _openai_max_retries(env_key: str, fallback_key: str = "LLM_MAX_RETRIES") -> int:
    raw = (os.getenv(env_key) or os.getenv(fallback_key) or "0").strip()
    return int(raw) if raw.isdigit() else 0


def judge_answer(question: str, correct_answer: str, response: str) -> bool:
    """Use the OpenAI-compatible OWL judge model to score one answer."""

    api_key, base_url, model_name = _load_judge_env()
    from openai import OpenAI

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        max_retries=_openai_max_retries("JUDGE_MAX_RETRIES"),
    )
    prompt = JUDGE_ANSWER.format(
        question=question,
        correct_answer=correct_answer,
        response=response,
    )
    completion = client.chat.completions.create(
        model=model_name,
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    content = completion.choices[0].message.content or ""
    match = re.search(r"(?im)^\s*correct:\s*(yes|no)\s*$", content)
    if not match:
        raise ValueError(f"LLM Judge response missing 'correct: yes/no': {content}")
    return match.group(1).lower() == "yes"


async def evaluate_task(
    task: dict,
    *,
    semaphore: asyncio.Semaphore | None = None,
) -> EvalOutcome:
    """Evaluate one task with the OWL LLM judge."""

    async def _run() -> EvalOutcome:
        task_id = task["question_ID"]
        try:
            passed = await asyncio.to_thread(
                judge_answer,
                task.get("question", ""),
                task.get("ground_truth", ""),
                task.get("model_prediction", ""),
            )
            return EvalOutcome(task_id=task_id, passed=passed)
        except Exception as exc:
            logger.error("LLM judge failed for task %s: %s", task_id, exc, exc_info=True)
            return EvalOutcome(task_id=task_id, passed=False, message=f"judge error: {exc}")

    if semaphore is None:
        return await _run()
    async with semaphore:
        return await _run()
