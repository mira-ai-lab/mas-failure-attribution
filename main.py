"""Entry point for running iterative attack/diagnosis attribution workflows.

This module orchestrates:
- task loading from a parquet dataset,
- round-based coding task execution,
- evaluation-driven branching into attack or diagnosis analysis,
- replay from recovery snapshots,
- and final attribution result persistence.
"""

# Standard library imports.
import argparse
import hashlib
import inspect
import importlib
import os
import re
import ast
from asyncio import Semaphore
import json
import shutil
import sys
from pathlib import Path
from typing import Type

# Third-party library imports.
from pipeline.eval.core import evaluate_round
from sandbox_fusion import set_dataset_endpoint, set_sandbox_endpoint
import asyncio
from tqdm.asyncio import tqdm

# Project-local imports: adapters and monitors.
from adapter.base_adapter import BaseAdapter
from monitor.attack_monitor import AttackMonitor
from monitor.base_monitor import BaseMonitor

# Project-local imports: pipeline stages.
from pipeline.coding.attack import attack_analysis, get_attack_analysis
from pipeline.coding.diagnose import diagnose_analysis, get_diagnose_analysis
from pipeline.runners.code_generation import run_code_generation_task
from pipeline.runners.web_research import run_web_research_task
from adapter.runtime_bootstrap import bootstrap_backend_runtime

# Project-local imports: utilities.
from utils.common import match_info, read_json_file, save_final_result, write_json_file
from utils.logging import handler
from utils.logging import logger

from utils.prompts import REPLAY_PROMPT
from dataset.dataset import (
    load_tasks,
)


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")



def _safe_path_component(value: str) -> str:
    """Return a deterministic filesystem-safe directory name for a task id."""

    _INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
    _RESERVED_WINDOWS_NAMES = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }

    raw = str(value)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    cleaned = _INVALID_PATH_CHARS.sub("_", raw).rstrip(" .")
    if not cleaned:
        cleaned = "task"
    if cleaned.split(".")[0].upper() in _RESERVED_WINDOWS_NAMES:
        cleaned = f"_{cleaned}"
    if len(cleaned) > 120:
        cleaned = f"{cleaned[:109].rstrip(' ._')}__{digest}"
    elif cleaned != raw:
        cleaned = f"{cleaned}__{digest}"
    return cleaned


def _load_backend(name: str) -> Type[BaseAdapter]:
    """Load a backend adapter class by backend name.

    Args:
        name: Backend identifier that maps to ``adapter.<name>.core``.

    Returns:
        A class object that implements the ``BaseAdapter`` interface.
    """
    backend = importlib.import_module(f"adapter.{name}.core")
    return getattr(backend, f'{name}Adapter')


WEB_TASK_SOURCES = {"gaia", "browsecomp", "assistantbench", "hotpotqa"}


def _task_runner(data_source: str):
    return run_web_research_task if data_source in WEB_TASK_SOURCES else run_code_generation_task


async def _execute_task_and_capture_trace(
    task,
    data_source,
    workspace_root,
    output_root,
    backend,
    skip_existing,
    semaphore=None,
):
    task_id = _safe_path_component(task["task_id"])
    workspace = workspace_root / data_source / f"round_0" / task_id
    output = output_root / data_source / f"round_0" / task_id
    workspace.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)

    logger.info("No recovery info, initializing new monitor...")
    recovery_path = output / "recovery"
    monitor = BaseMonitor(recovery_path, workspace, backend)
    runner = _task_runner(data_source)
    runner_kwargs = {
        "skip_existing": skip_existing,
        "monitor": monitor,
        "semaphore":semaphore
    }
    result = runner(
        task,
        workspace,
        output,
        backend,
        **runner_kwargs,
    )
    if inspect.isawaitable(result):
        return await result
    return result


async def main(args):
    """Run the full multi-round attribution pipeline.

    The function performs round-0 execution, evaluates outcomes, and then
    iteratively applies attack or diagnosis analysis based on previous-round
    evaluation results. It replays tasks from recovery snapshots and persists
    final attribution records when behavior flips between rounds.

    Args:
        args: Parsed CLI arguments used to configure dataset, backend,
            workspace/output directories, and round execution controls.
    """
    bootstrap_backend_runtime(args.backend, getattr(args, "env_file", None))

    # load datasets
    tasks = load_tasks(
        args.dataset,
        sample_offset=args.sample_offset,
        max_samples=args.max_samples,
        skip_task_ids=getattr(args, "skip_task_ids", ""),
    )
    if not tasks:
        logger.info("No tasks selected; exiting.")
        return
    
    Backend = _load_backend(args.backend)
    backend = Backend()

    data_source = tasks[0]["data_source"]
    workspace_root: Path = args.workspace
    output_root: Path = args.output

    skip_existing = args.skip_existing
    max_rounds = args.max_rounds
    rollout_only = args.rollout
    run_attack = args.run_mode in ("all", "attack")
    run_diagnose = args.run_mode in ("all", "diagnose")
    logger.info(
        f"run_mode={args.run_mode}: attack={'on' if run_attack else 'off'}, "
        f"diagnose={'on' if run_diagnose else 'off'}"
    )

    use_concurrency = args.concurrent is not None and args.concurrent > 1
    concurrency = args.concurrent if use_concurrency else 1

    # covert to absolute path
    if not workspace_root.is_absolute():
        workspace_root = workspace_root.absolute()
    if not output_root.is_absolute():
        output_root = output_root.absolute()

    # ROUND 0: run without injecting / diagnosing
    semaphore = Semaphore(concurrency)
    coros = [
        _execute_task_and_capture_trace(
            task,
            data_source,
            workspace_root,
            output_root,
            backend,
            skip_existing=skip_existing,
            semaphore=semaphore,
        )
        for task in tasks
    ]

    eval_path = output_root / data_source / "round_0"
    await tqdm.gather(*coros)

    eval_results, msg_results = await evaluate_round(
        eval_path,
        data_source=data_source,
        backend=args.backend,
        semaphore=semaphore,
        skip_existing=skip_existing,
    )
    
    if rollout_only:
        return


    
    # step2 multi-point injection and playback verification TODO：重构代码，现在太长了，抽象出单独的函数
    # ROUND i >= 1: divide eval results of round 0 into success / fail
    # sucess -> attack pipeline
    # fail -> diagnose pipeline
    # current implementation is for testing replay function
    completed_tasks = []

    for current in range(1, max_rounds + 1):
        length = len(tasks)
        batch_size = concurrency * 100
        for i in range(0, length + batch_size, batch_size):
            coros = []
            for task in tasks[i:i+batch_size]:       

                async def _run(task):
                    task_key = task["task_id"]
                    task_id = _safe_path_component(task_key)
                    if task_id in completed_tasks:
                        return
                    
                    logger.info(f'Round {current}: Start to process Task {task_id}')
                    last_round_output = output_root / data_source / f"round_{current-1}" / task_id
                    if not last_round_output.exists():
                        raise FileNotFoundError(f'last round output not exists for {task_id}')

                    last_round_log = read_json_file(last_round_output / 'log.json') 
                    last_round_log = str(last_round_log).replace(f'round_{current-1}', f'round_{current}')
                    last_round_log = ast.literal_eval(last_round_log)
                    output = output_root / data_source / f"round_{current}" / task_id
                    output.mkdir(parents=True, exist_ok=True)
                    log = output / 'log.json'
                    if log.exists():
                        if skip_existing:
                            logger.info(f'Log for task {task_id} exists, skipping this round...')
                            return
                    
                    # step 2 branch 1: sucess -> attack pipeline
                    if eval_results[task_key]:
                        if not run_attack:
                            logger.info(
                                f'run_mode={args.run_mode}: skip attack for success task {task_id}'
                            )
                            shutil.copytree(last_round_output, output, dirs_exist_ok=True,)
                            return

                        logger.info(f'Last round processed as success for {task_id}, start the attack process...')
                        
                        workspace = workspace_root / data_source / f"round_{current}" / f'{task_id}_attack_analysis'
                        workspace.mkdir(parents=True, exist_ok=True)
                        previous_injections_path = last_round_output / 'attack_analysis.json'
                        previous_injections = (
                            read_json_file(previous_injections_path) 
                                if previous_injections_path.exists() else []
                        )

                        is_success = await attack_analysis(
                            task=last_round_log,
                            workspace=workspace,
                            output=output,
                            backend=backend,
                            skipping_exists=skip_existing,
                            injection_history=previous_injections,
                            message=msg_results.get(task_key, ""),
                            semaphore=semaphore
                        )

                        if not is_success:
                            logger.info(f'Attack Analysis Failed, skipping this round...')
                            shutil.copytree(
                                last_round_output,
                                output,
                                dirs_exist_ok=True
                            )
                            return

                        replay_info = get_attack_analysis(output)

                    # step 2 branch 2: fail -> diagnose pipeline
                    else:
                        if not run_diagnose:
                            logger.info(
                                f'run_mode={args.run_mode}: skip diagnose for failed task {task_id}'
                            )
                            shutil.copytree(
                                last_round_output,
                                output,
                                dirs_exist_ok=True,
                            )
                            return

                        logger.info(f'Last round processed as failure for {task_id}, start the diagnosis process...')
                        
                        workspace = workspace_root / data_source / f"round_{current}" / f'{task_id}_diagnose_analysis'
                        workspace.mkdir(parents=True, exist_ok=True)

                        previous_injections_path = last_round_output / 'diagnose_analysis.json'
                        previous_injections = (
                            read_json_file(previous_injections_path) 
                                if previous_injections_path.exists() else []
                        )

                        is_success = await diagnose_analysis(
                            task=last_round_log,
                            workspace=workspace,
                            output=output,
                            backend=backend,
                            skipping_exists=skip_existing,
                            injection_history=previous_injections,
                            message=msg_results.get(task_key, ""),
                            semaphore=semaphore,
                            diagnose_mode=args.diagnose_mode,
                        )
                        if not is_success:
                            logger.info(f'Diagnose Analysis Failed, skipping this round...')
                            shutil.copytree(
                                last_round_output,
                                output,
                                dirs_exist_ok=True
                            )
                            return
                        replay_info = get_diagnose_analysis(output)
                    
                    replay_recovery_path = last_round_output / "recovery"
                    monitor = AttackMonitor(
                        replay_recovery_path,
                        workspace,
                        backend,
                        replay_info[-1],
                        last_round_log,
                    )
                    runner = _task_runner(data_source)
                    runner_kwargs = {
                        "skip_existing": skip_existing,
                        "monitor": monitor,
                        "semaphore": semaphore
                    }
                    result = runner(
                        task,
                        workspace,
                        output,
                        backend,
                        **runner_kwargs,
                    )
                    if inspect.isawaitable(result):
                        result = await result
                    if not result:
                        logger.info(f'Replay Failed, skipping this round...')
                        shutil.copytree(
                            last_round_output,
                            output,
                            dirs_exist_ok=True
                        )
                        return
                
                coros.append(_run(task))
        
            await tqdm.gather(*coros)

        # save last round's eval results
        last_eval_results = eval_results
        eval_path = output_root / data_source / f"round_{current}"
        eval_results, msg_results = await evaluate_round(
            eval_path,
            data_source=data_source,
            backend=args.backend,
            semaphore=semaphore,
            skip_existing=skip_existing,
        )
        
        for task_key in eval_results:
            if task_key not in last_eval_results:
                continue
            task_id = _safe_path_component(task_key)
            # step 2  behavior flip -> final attribution
            if eval_results[task_key] ^ last_eval_results[task_key]:
                output = output_root / data_source / f"round_{current}" / task_id
                if last_eval_results[task_key]: # from success to fail, 
                    if not run_attack:
                        logger.info(
                            f'run_mode={args.run_mode}: skip attack finalization for {task_id}'
                        )
                        continue
                    logger.info(f'[Round {current}] Attack result eval changed to failure, diagnosing...')
                    last_round_output = output_root / data_source / f"round_{current}" / task_id
                    if not last_round_output.exists():
                        raise FileNotFoundError(f'last round output not exists for {task_id}')
                    last_round_log = read_json_file(last_round_output / 'log.json') 
                   
                    try:
                        final_info = get_attack_analysis(output)
                        analysis_source_log = last_round_log
                    except Exception as e:
                        logger.error(f"Error occurred while analyzing attack results for {task_id}: {e}")
                        continue
    
                else: # from fail to success
                    if not run_diagnose:
                        logger.info(
                            f'run_mode={args.run_mode}: skip diagnose finalization for {task_id}'
                        )
                        continue
                    last_round_output = output_root / data_source / f"round_{current-1}" / task_id
                    if not last_round_output.exists():
                        raise FileNotFoundError(f'last round output not exists for {task_id}')
                    last_round_log = read_json_file(last_round_output / 'log.json') 
                    try:
                        final_info = get_diagnose_analysis(output)
                        analysis_source_log = last_round_log
                    except Exception as e:
                        logger.error(f"Error occurred while analyzing diagnose results for {task_id}: {e}")
                        continue

                completed_tasks.append(task_id)
                try:
                    save_final_result(output_root / 'final_results', analysis_source_log, final_info)
                except ValueError as e:
                    logger.error(f'Final attribution rejected for {task_id}: {e}')
            else:
                logger.info(f'Eval Result remains the same, Attack/Diagnose Fail...')

if __name__ == "__main__":
    try:
        handler.doRollover()
    except OSError as e:
        logger.warning(f"Skipping log rollover because the log file is busy: {e}")
    # set_sandbox_endpoint("http://localhost:8080/")
    # set_dataset_endpoint("http://localhost:8080/online_judge/")
    parser = argparse.ArgumentParser(description="Universal attack and diagnosis framework")
    # 设置数据集
    parser.add_argument(
        "--dataset",
        type=str,
        required=True,
        help="Registered dataset alias only (e.g. kodcode, gaia, hotpotqa, humaneval, mbpp, swebench)",
    )
    parser.add_argument(
        "--skip_task_ids",
        type=str,
        default="",
        help="Comma-separated task IDs to skip (e.g. task_1,task_2)",
    )

    # 设置多智能体系统
    parser.add_argument("--backend", type=str, required=True,help="MAS backend name",)
    parser.add_argument("--workspace", type=Path, required=True, help="Working directory path")
    parser.add_argument("--output", type=Path, required=True, help="Output directory path")
    parser.add_argument("--max_rounds", type=int, default=3, help="Maximum number of rounds")
    parser.add_argument("--max_samples", type=int, default=None, help="Maximum number of samples")
    parser.add_argument("--sample_offset", type=int, default=0, help="Start from this dataset row offset")
    
    parser.add_argument("--env_file", type=Path, default=None, help="OpenAI-compatible API env file for this run")

    # TODO: concurrency
    parser.add_argument("--concurrent", type=int, default=None, help="Enable concurrent processing")
    parser.add_argument("--skip_existing", "-s", action="store_true")
    parser.add_argument("--rollout", action="store_true")
    parser.add_argument(
        "--run-mode",
        type=str,
        choices=["all", "attack", "diagnose"],
        default="all",
        help=(
            "Pipeline branch control: all (success->attack, fail->diagnose), "
            "attack (only attack on success), diagnose (only diagnose on failure)"
        ),
    )
    parser.add_argument(
        "--diagnose-mode",
        type=str,
        choices=["default", "critic"],
        default="default",
        help="Diagnose analysis prompt mode: default (direct) or critic (CRITIC tool-interactive)",
    )

    args = parser.parse_args()
    asyncio.run(main(args))
