"""LLM-judge scorers for web-research datasets."""

from __future__ import annotations

import asyncio
import os
import re

from pipeline.eval.types import EvalOutcome
from utils.owl_runtime import ensure_owl_runtime_available


BROWSECOMP_GRADER_TEMPLATE = """
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


def _load_judge_env() -> None:
    ensure_owl_runtime_available(require_api_keys=True)


def judge_answer(question: str, correct_answer: str, response: str) -> bool:
    """Use the OpenAI-compatible OWL judge model to score one answer."""

    _load_judge_env()
    from openai import OpenAI

    client = OpenAI(
        api_key=os.environ["VLLM_API_KEY"],
        base_url=os.environ["VLLM_API_URL"],
    )
    prompt = BROWSECOMP_GRADER_TEMPLATE.format(
        question=question,
        correct_answer=correct_answer,
        response=response,
    )
    completion = client.chat.completions.create(
        model=os.environ["VLLM_MODEL_NAME"],
        messages=[{"role": "user", "content": prompt}],
        temperature=float(os.getenv("BROWSECOMP_JUDGE_TEMPERATURE", "0")),
    )
    content = completion.choices[0].message.content or ""
    match = re.search(r"(?im)^\s*correct:\s*(yes|no)\s*$", content)
    if not match:
        raise ValueError(f"BrowseComp judge response missing 'correct: yes/no': {content}")
    return match.group(1).lower() == "yes"


async def evaluate_task(
    task: dict,
    *,
    semaphore: asyncio.Semaphore | None = None,
) -> EvalOutcome:
    """Evaluate one task with the OWL LLM judge."""

    async def _run() -> EvalOutcome:
        passed = await asyncio.to_thread(
            judge_answer,
            task.get("question", ""),
            task.get("ground_truth", ""),
            task.get("model_prediction", ""),
        )
        return EvalOutcome(task_id=task["question_ID"], passed=passed)

    if semaphore is None:
        return await _run()
    async with semaphore:
        return await _run()
