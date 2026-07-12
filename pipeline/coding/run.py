"""Core coding-task execution and replay helpers for each round."""

from asyncio import Semaphore
from enum import Enum
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Type
from urllib.parse import unquote, urlparse

from tqdm.asyncio import tqdm

from adapter.base_adapter import BaseAdapter
from model.schema import History
from monitor.attack_monitor import AttackMonitor
from monitor.base_monitor import BaseMonitor, RoleType
from utils.common import dumps, read_json_file
from utils.logging import logger

class RunMode(Enum):
    """Execution mode hints for task running strategies."""

    NONE = 0
    ATTACK = 1
    DIAGNOSE = 2

async def run_coding_task(
    task: dict,
    workspace: Path,
    output: Path,
    backend: BaseAdapter,
    recovery_dir: Path = None,
    skip_existing: bool = True,
    monitor: BaseMonitor = None,
    semaphore: Semaphore = None,
):
    """
    Execute one coding task and persist structured execution logs.

    Args:
        task: Dataset task record.
        workspace: Workspace directory for backend execution.
        output: Output directory for logs and artifacts.
        backend: Backend adapter implementing run/serialize APIs.
        recovery_dir: Optional recovery directory for backend resume.
        skip_existing: Whether to skip when ``log.json`` already exists.
        monitor: Execution monitor used to capture history and topology.
        replay_info: Prompt prefix used during replayed runs.

    Returns:
        ``True`` when run completes and logs are saved, otherwise ``False``.
    """
    async with semaphore:
        data_source = task["data_source"]
        task_id = task["task_id"]
        signature = task["reference_solution"].split("\n")[0]
        idea = (
            task["question"]
            + f"I wish you finish the task with a multi-agent cooperation"
            + f"The file name of your solution MUST be 'solution.py' and MUST be located at root directory"
            + f'The signature of function is {signature}'
        )
        log = output / 'log.json'
        if log.exists():
            log_content = read_json_file(log)
            if log_content['history'] == []:
                shutil.rmtree(output, ignore_errors=True)
                output.mkdir(parents=True, exist_ok=True)
            elif skip_existing:
                logger.info(f'Log for task {task_id} exists, skipping this round...')
                return True
            else:
                logger.info(f'Log for task {task_id} exists, overriding...')
                shutil.rmtree(workspace, ignore_errors=True)
                shutil.rmtree(output, ignore_errors=True)
                workspace.mkdir(parents=True, exist_ok=True)
                output.mkdir(parents=True, exist_ok=True)
        try:
            await backend.run_backend(
                idea=idea,
                workspace=workspace,
                recovery=recovery_dir,
                monitor=monitor
            )
        except Exception as e:
            logger.error(f"Error running task {data_source}/{task_id}: {e}")
        
        logger.info(f'Task {data_source}/{task_id} ends executing...')
        solution_path = workspace / 'solution.py'
        if solution_path.exists():
            model_prediction = solution_path.read_text()
        else:
            model_prediction = ""
            logger.warning(f'solution.py not found for task {data_source}/{task_id}')
        
        prompt_map = backend.get_prompt_map()

        used_roles = set(h.name for h in monitor.history)
        system_prompts = {
            name: prompt_map[name]
            for name in used_roles
        }

    with open(log, "w", encoding="utf-8") as f:
        json.dump(
            {
                "question": task["question"],
                "question_ID": task["task_id"],
                "ground_truth": task["reference_solution"],
                "test": task["test"],
                "model_prediction": model_prediction,
                "history": dumps(monitor.history),
                "topology": dumps(monitor.topology),
                "system_prompts": system_prompts,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return True


def _extract_browsecomp_exact_answer(raw_answer: str) -> str:
    match = re.search(
        r"(?ims)^Exact Answer:\s*(.*?)(?:^\s*Confidence:|\Z)",
        raw_answer or "",
    )
    if not match:
        return ""
    answer = match.group(1).strip()
    if not answer or answer.startswith("{") or answer.lower() == "none":
        return ""
    return answer


def _message_content(message) -> str:
    """Extract text content from CAMEL BaseMessage-like objects."""
    if message is None:
        return ""
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    return "" if content is None else str(content)


def _wikipedia_entity_from_url(document_path: str) -> str:
    """Return a Wikipedia entity title for ordinary wiki article URLs."""
    parsed = urlparse(document_path)
    host = parsed.netloc.lower()
    if not host.endswith("wikipedia.org"):
        return ""
    marker = "/wiki/"
    if marker not in parsed.path:
        return ""
    title = parsed.path.split(marker, 1)[1].split("/", 1)[0]
    title = unquote(title).replace("_", " ").strip()
    if not title or ":" in title:
        return ""
    return title


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


def run_gaia_task(
    task: dict,
    workspace: Path,
    output: Path,
    backend: BaseAdapter,
    recovery_dir: Path = None,
    skip_existing: bool = True,
    monitor: BaseMonitor = None,
):
    """Execute one GAIA task with OWL's native GAIA role-playing runtime."""
    data_source = task["data_source"]
    task_id = task["task_id"]
    file_hint = ""
    if task.get("file_name"):
        file_hint = f"\nNecessary file: {task['file_name']}"
    idea = task["question"] + file_hint
    if data_source == "browsecomp":
        idea = f"{idea}\n\n{BROWSECOMP_RESEARCH_INSTRUCTIONS}"
    elif data_source == "assistantbench":
        idea = _assistantbench_task_prompt(task["question"] + file_hint)
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
    try:
        old_cwd = Path.cwd()
        os.chdir(workspace)
        try:
            owl_repo = Path(os.environ.get("OWL_REPO_PATH", "D:/code_re/mas-failure-attribution/owl"))
            if str(owl_repo) not in sys.path:
                sys.path.insert(0, str(owl_repo))
            try:
                from dotenv import load_dotenv

                env_file = os.environ.get("MAS_FA_ENV_FILE")
                if env_file:
                    load_dotenv(Path(env_file), override=True)
                else:
                    load_dotenv(owl_repo / "owl" / ".env.gaia", override=True)
                load_dotenv(owl_repo / "owl" / ".env", override=False)
            except Exception:
                pass

            from camel.models import ModelFactory
            from camel.toolkits import (
                CodeExecutionToolkit,
                ExcelToolkit,
                FileToolkit,
                FunctionTool,
                ImageAnalysisToolkit,
                SearchToolkit,
            )
            from camel.types import ModelPlatformType
            from owl.utils import DocumentProcessingToolkit
            from owl.utils.enhanced_role_playing import OwlGAIARolePlaying, run_society
            from owl.utils.common import extract_pattern

            model = ModelFactory.create(
                model_platform=ModelPlatformType.OPENAI_COMPATIBLE_MODEL,
                model_type=os.environ["VLLM_MODEL_NAME"],
                url=os.environ["VLLM_API_URL"],
                api_key=os.environ["VLLM_API_KEY"],
                model_config_dict={"temperature": float(os.getenv("OWL_TEMPERATURE", "0"))},
            )
            document_toolkit = DocumentProcessingToolkit(model=model)
            image_toolkit = ImageAnalysisToolkit(model=model)
            search_toolkit = SearchToolkit()
            code_toolkit = CodeExecutionToolkit(sandbox="subprocess", verbose=True)
            excel_toolkit = ExcelToolkit()
            file_toolkit = FileToolkit()

            def extract_document_content(document_path: str):
                entity = _wikipedia_entity_from_url(document_path)
                if entity:
                    return search_toolkit.search_wiki(entity)
                return document_toolkit.extract_document_content(document_path)

            assistant_tools = [
                FunctionTool(search_toolkit.search_duckduckgo),
                FunctionTool(search_toolkit.search_wiki),
                FunctionTool(extract_document_content),
                FunctionTool(image_toolkit.ask_question_about_image),
                FunctionTool(code_toolkit.execute_code),
                FunctionTool(excel_toolkit.extract_excel_content),
                *file_toolkit.get_tools(),
            ]
            assistant_agent_kwargs = {
                "model": model,
                "tools": assistant_tools,
                "max_iteration": int(os.getenv("OWL_ASSISTANT_MAX_ITERATION", "12")),
                "step_timeout": float(os.getenv("OWL_ASSISTANT_STEP_TIMEOUT", "300")),
                "tool_execution_timeout": float(os.getenv("OWL_TOOL_EXECUTION_TIMEOUT", "90")),
            }
            society = OwlGAIARolePlaying(
                task_prompt=idea,
                with_task_specify=False,
                user_role_name="user",
                assistant_role_name="assistant",
                user_agent_kwargs={"model": model},
                assistant_agent_kwargs=assistant_agent_kwargs,
            )
            system_prompts = {
                "user": _message_content(getattr(society, "user_sys_msg", None)),
                "assistant": _message_content(getattr(society, "assistant_sys_msg", None)),
            }
            round_limit = int(os.getenv("OWL_GAIA_ROUND_LIMIT", "6"))
            raw_answer, chat_history, token_info = run_society(
                society,
                round_limit=round_limit,
            )
            try:
                model_prediction = extract_pattern(raw_answer, "final_answer")
            except Exception:
                model_prediction = ""
            if data_source == "browsecomp" and not model_prediction:
                model_prediction = _extract_browsecomp_exact_answer(raw_answer)
            if model_prediction is None:
                model_prediction = ""
            if not model_prediction and len(chat_history) >= round_limit:
                logger.error(
                    "GAIA-style task %s/%s reached round_limit=%s without final_answer; "
                    "not writing a successful log.",
                    data_source,
                    task_id,
                    round_limit,
                )
                return False

            if not chat_history:
                logger.error(
                    "GAIA-style task %s/%s produced no execution history; "
                    "not writing a successful log.",
                    data_source,
                    task_id,
                )
                return False

            if monitor is not None:
                monitor.record_topology("user", "assistant")
                for item in chat_history:
                    user_content = item.get("user", "")
                    assistant_content = item.get("assistant", "")
                    tool_calls = item.get("tool_calls") or []
                    if user_content:
                        monitor.record_step(user_content, "user", RoleType.ASSISTANT)
                    if tool_calls:
                        assistant_content += "\n\nTool calls:\n" + json.dumps(
                            tool_calls,
                            ensure_ascii=False,
                            indent=2,
                        )
                    if assistant_content:
                        monitor.record_step(
                            assistant_content,
                            "assistant",
                            RoleType.ASSISTANT,
                        )
            (workspace / "raw_answer.txt").write_text(raw_answer, encoding="utf-8")
            (workspace / "final_answer.txt").write_text(model_prediction, encoding="utf-8")
            (workspace / "token_info.json").write_text(
                json.dumps(token_info, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        finally:
            os.chdir(old_cwd)
    except Exception as e:
        logger.error(f"Error running GAIA task {data_source}/{task_id}: {e}")
        return False

    logger.info(f"Task {data_source}/{task_id} ends executing...")
    history = monitor.history if monitor is not None else []
    topology = monitor.topology if monitor is not None else {}

    with open(log, "w", encoding="utf-8") as f:
        json.dump(
            {
                "question": task["question"],
                "question_ID": task["task_id"],
                "ground_truth": task["reference_solution"],
                "test": task.get("test", ""),
                "model_prediction": model_prediction,
                "history": dumps(history),
                "topology": dumps(topology),
                "system_prompts": {
                    "user": system_prompts.get("user", ""),
                    "assistant": system_prompts.get("assistant", ""),
                },
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return True
        with open(log, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "question": task["question"],
                    "question_ID": task["task_id"],
                    "ground_truth": task["reference_solution"],
                    "test": task["test"],
                    "model_prediction": model_prediction,
                    "history": dumps(monitor.history),
                    "topology": dumps(monitor.topology),
                    "system_prompts": system_prompts,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
    return True
