"""Recover eval + finalization for a round whose rollout finished but eval crashed.

Usage:
    python recover_round_eval.py \
        --run math_captain_p1_20260729_183930 \
        --round 2 \
        --dataset math \
        --backend Captain \
        --env_file config/env
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
from pathlib import Path

from adapter.runtime_bootstrap import bootstrap_backend_runtime
from pipeline.eval.core import evaluate_round, load_eval_results
from pipeline.coding.attack import get_attack_analysis
from pipeline.coding.diagnose import get_diagnose_analysis
from utils.common import read_json_file, save_final_result
from utils.logging import logger

_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _safe_path_component(value: str) -> str:
    raw = str(value)
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    safe = _INVALID_PATH_CHARS.sub("_", raw).strip(". ")
    if not safe or safe.upper() in _RESERVED_WINDOWS_NAMES:
        safe = f"task_{digest}"
    return safe


async def recover(run_tag: str, round_num: int, dataset: str, backend: str, env_file: Path) -> None:
    bootstrap_backend_runtime(backend, env_file)

    output_root = Path("output") / run_tag
    data_source = dataset
    run_attack = True
    run_diagnose = True

    prev_round = round_num - 1
    prev_eval_path = output_root / data_source / f"round_{prev_round}"
    cur_eval_path = output_root / data_source / f"round_{round_num}"

    if not cur_eval_path.exists():
        logger.error("Round %d output dir not found: %s", round_num, cur_eval_path)
        return

    logger.info("Loading previous round %d eval results from %s ...", prev_round, prev_eval_path)
    last_eval_results, _ = load_eval_results(prev_eval_path, data_source)
    logger.info("Loaded %d prev eval entries.", len(last_eval_results))

    logger.info("Running eval for round %d in %s ...", round_num, cur_eval_path)
    semaphore = asyncio.Semaphore(1)
    eval_results, msg_results = await evaluate_round(
        cur_eval_path,
        data_source=data_source,
        backend=backend,
        semaphore=semaphore,
        skip_existing=False,
    )
    logger.info("Round %d eval done: %d entries.", round_num, len(eval_results))

    flipped = 0
    for task_key, passed in eval_results.items():
        if task_key not in last_eval_results:
            continue
        task_id = _safe_path_component(task_key)
        if passed ^ last_eval_results[task_key]:
            output = cur_eval_path / task_id
            if last_eval_results[task_key]:
                if not run_attack:
                    logger.info("skip attack finalization for %s", task_id)
                    continue
                logger.info("[Round %d] %s: success->fail, diagnosing attack...", round_num, task_id)
                last_round_output = cur_eval_path / task_id
                if not last_round_output.exists():
                    logger.error("last round output missing for %s", task_id)
                    continue
                last_round_log = read_json_file(last_round_output / "log.json")
                try:
                    final_info = get_attack_analysis(output)
                    analysis_source_log = last_round_log
                except Exception as e:
                    logger.error("attack analysis failed for %s: %s", task_id, e)
                    continue
            else:
                if not run_diagnose:
                    logger.info("skip diagnose finalization for %s", task_id)
                    continue
                logger.info("[Round %d] %s: fail->success, diagnosing...", round_num, task_id)
                last_round_output = output_root / data_source / f"round_{prev_round}" / task_id
                if not last_round_output.exists():
                    logger.error("prev round output missing for %s", task_id)
                    continue
                last_round_log = read_json_file(last_round_output / "log.json")
                try:
                    final_info = get_diagnose_analysis(output)
                    analysis_source_log = last_round_log
                except Exception as e:
                    logger.error("diagnose analysis failed for %s: %s", task_id, e)
                    continue

            flipped += 1
            try:
                save_final_result(output_root / "final_results", analysis_source_log, final_info)
                logger.info("Saved final result for %s", task_id)
            except ValueError as e:
                logger.error("Final attribution rejected for %s: %s", task_id, e)
        else:
            logger.info("[Round %d] %s: no flip (%s)", round_num, task_id, "pass" if passed else "fail")

    logger.info("Recovery complete. Flipped tasks: %d", flipped)


def main() -> None:
    parser = argparse.ArgumentParser(description="Recover eval + finalization for a crashed round")
    parser.add_argument("--run", required=True, help="Run tag (e.g. math_captain_p1_20260729_183930)")
    parser.add_argument("--round", type=int, required=True, help="Round number to recover (e.g. 2)")
    parser.add_argument("--dataset", required=True, help="Dataset alias (e.g. math)")
    parser.add_argument("--backend", required=True, help="Backend name (e.g. Captain)")
    parser.add_argument("--env_file", type=Path, default=Path("config/env"), help="Env file for judge API")
    args = parser.parse_args()
    asyncio.run(recover(args.run, args.round, args.dataset, args.backend, args.env_file))


if __name__ == "__main__":
    main()
