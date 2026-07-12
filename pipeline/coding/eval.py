"""Task evaluation utilities based on sandboxed correctness execution."""

import asyncio
import base64
import json
import os
from pathlib import Path
import re
import shutil

from tqdm.asyncio import tqdm
from utils.common import read_json_file
from utils.logging import logger
from sandbox_fusion import (
    RunCodeRequest,
    RunStatus,
    run_code,
    run_code_async,
)

def code_exec(code: str, test: str):
    """Execute candidate solution with provided tests in sandbox runtime."""

    base64_content = base64.b64encode(code.encode("utf-8")).decode("utf-8")
    request = RunCodeRequest(
        compile_timeout=60,
        run_timeout=60,
        language="pytest",
        code=test,
        files={"solution.py": base64_content},
    )
    try:
        return run_code(request, client_timeout=60)
    except Exception as e:
        # Log exception details
        logger.error(f"Error in code_exec_async: {e}", exc_info=True)
        # Depending on needs, you can return a custom error object or None
        print(f"Error in code_exec_async: {e}")
        return None


async def code_exec_async(code: str, test: str):
    base64_content = base64.b64encode(code.encode("utf-8")).decode("utf-8")
    request = RunCodeRequest(
        compile_timeout=60,
        run_timeout=60,
        language="pytest",
        code=test,
        files={"solution.py": base64_content},
    )
    try:
        return await run_code_async(request, client_timeout=60)
    except Exception as e:
        # Log exception details
        logger.error(f"Error in code_exec_async: {e}", exc_info=True)
        # Depending on needs, you can return a custom error object or None
        print(f"Error in code_exec_async: {e}")
        return None

async def run_correctness_eval_task(
    task: dict, semaphore: asyncio.Semaphore = None
) -> tuple[str, bool]:
    """Run one task evaluation and return ``(task_id, passed)``."""
    async with semaphore:
        solution = task["model_prediction"]
        test = task["test"]
        result = await code_exec_async(solution, test)
        if result is None:
            logger.error(f"Code execution failed for task {task['question_ID']}")
            return task["question_ID"], False, "Code execution error"
        logger.info(f"Eval result for task {task['question_ID']}: {result.status == RunStatus.Success}, message: {str(result)}")
        return task["question_ID"], result.status == RunStatus.Success, result.run_result.stdout


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
    import string

    if input_str is None:
        return ""
    no_spaces = str(input_str).lower().strip()
    if remove_punct:
        trans_table = str.maketrans("", "", string.punctuation)
        no_spaces = no_spaces.translate(trans_table)
    return no_spaces


def _split_string(s: str, char_list: list[str] | None = None) -> list[str]:
    import re

    if char_list is None:
        char_list = [",", ";"]
    pattern = f"[{''.join(char_list)}]"
    return re.split(pattern, s)


def gaia_score(model_answer: str, ground_truth: str) -> bool:
    """GAIA scorer compatible with OWL's benchmark scorer."""
    model_answer = "" if model_answer is None else str(model_answer)
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


def run_gaia_eval_task(task: dict) -> tuple[str, bool]:
    return task["question_ID"], gaia_score(
        task.get("model_prediction", ""),
        task.get("ground_truth", ""),
    )


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


def _load_browsecomp_judge_env():
    try:
        from dotenv import load_dotenv
    except ImportError as e:
        raise RuntimeError("python-dotenv is required for BrowseComp LLM judge") from e

    project_root = Path(__file__).resolve().parents[2]
    owl_root = Path(os.environ.get("OWL_REPO_PATH", project_root / "owl"))
    env_file = os.environ.get("MAS_FA_ENV_FILE")
    if env_file:
        load_dotenv(Path(env_file), override=True)
    else:
        load_dotenv(owl_root / "owl" / ".env.gaia", override=True)
    load_dotenv(owl_root / "owl" / ".env", override=False)

    missing = [
        key
        for key in ("VLLM_API_URL", "VLLM_MODEL_NAME", "VLLM_API_KEY")
        if not os.environ.get(key)
    ]
    if missing:
        raise RuntimeError(f"Missing BrowseComp judge env vars: {missing}")


def _judge_browsecomp_answer(question: str, correct_answer: str, response: str) -> bool:
    _load_browsecomp_judge_env()
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


def run_browsecomp_eval_task(task: dict) -> tuple[str, bool]:
    return task["question_ID"], _judge_browsecomp_answer(
        question=task.get("question", ""),
        correct_answer=task.get("ground_truth", ""),
        response=task.get("model_prediction", ""),
    )


def run_assistantbench_eval_task(task: dict) -> tuple[str, bool]:
    return task["question_ID"], _judge_browsecomp_answer(
        question=task.get("question", ""),
        correct_answer=task.get("ground_truth", ""),
        response=task.get("model_prediction", ""),
    )


def run_eval_tasks(
async def run_eval_tasks(
    eval_path: Path,
    data_source: str,
    semaphore: asyncio.Semaphore = None,
    skip_existing: bool = True,
) -> dict[str, bool]:
    """
    Evaluate all task logs under one round directory and save pass/fail map.

    Args:
        eval_path: Directory containing per-task subdirectories with ``log.json``.
        data_source: Dataset source name used in evaluation output filename.
        skip_existing: Whether to skip when evaluation result already exists.

    Returns:
        Mapping from task id to pass/fail status, or ``None`` when skipped.
    """
    logger.info(f'Starting to eval tasks in {eval_path}...')
    filename = f"eval_{data_source}.json"
    save_path = eval_path / filename
    msg_path = eval_path / f'eval_msg_{data_source}.json'
    if save_path.exists():
        if skip_existing:
            logger.info(f'Eval result for {data_source} exists, skipping...')
            return
        else:
            logger.info(f'Eval result for {data_source} exists, overriding...')

    eval_log_paths = [p / 'log.json' for p in eval_path.iterdir() if p.is_dir()]
    tasks = [read_json_file(p) for p in eval_log_paths if p.exists()]
    if data_source in {"gaia", "hotpotqa"}:
        results = [run_gaia_eval_task(task) for task in tasks]
    elif data_source == "browsecomp":
        results = [run_browsecomp_eval_task(task) for task in tasks]
    elif data_source == "assistantbench":
        results = [run_assistantbench_eval_task(task) for task in tasks]
    else:
        results = [run_correctness_eval_task(task) for task in tasks]
    results = {task_id: result for task_id, result in results}

    coros = [run_correctness_eval_task(task, semaphore) for task in tasks]
    results = await tqdm.gather(*coros)
    status = {task_id: result for task_id, result,_ in results}
    messages = {task_id: message for task_id, _, message in results}
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w") as f:
        json.dump(status, f)
    with open(msg_path, "w") as f:
        json.dump(messages, f)
    logger.info(f'Ending to eval tasks in {eval_path}, results saved in {save_path}...')

def load_eval_results(
    eval_path: Path,
    data_source: str,
):
    """Load previously saved evaluation result mapping for one round."""
    eval_results = read_json_file(eval_path / f"eval_{data_source}.json")
    msg_results = read_json_file(eval_path / f"eval_msg_{data_source}.json")
    return eval_results, msg_results
    
