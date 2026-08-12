#!/usr/bin/env python3
"""Re-run attack pipeline for meta-leakage samples using seeded round_0 logs.

Reads task list from ``output/meta_leakage_audit_report.json`` (produced by
``scripts/audit_meta_leakage.py``), copies each task's round_0 artifacts from
the original batch run, then re-runs attack analysis + replay with v1 prompts.

Does **not** modify ``main.py``. Prompt version is switched via monkeypatch.

Usage:
    # Preview tasks to rerun
    python scripts/rerun_leaked_attack.py --dry-run

    # Rerun all leaked attack samples with v1 prompts
    python scripts/rerun_leaked_attack.py \\
        --output output/math_captain_prompt_v1_rerun \\
        --workspace workspace/math_captain_prompt_v1_rerun \\
        --env_file config/env \\
        --prompt-version v1

    # Rerun a subset
    python scripts/rerun_leaked_attack.py \\
        --task-ids task_eada88b86028,task_ef0b22c43c29 \\
        --output output/math_captain_prompt_v1_pilot \\
        --workspace workspace/math_captain_prompt_v1_pilot \\
        --env_file config/env
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import importlib
import inspect
import json
import re
import shutil
import sys
from asyncio import Semaphore
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_INVALID_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

DEFAULT_AUDIT_REPORT = ROOT / "output" / "meta_leakage_audit_report_original_batches.json"
FALLBACK_AUDIT_REPORT = ROOT / "output" / "meta_leakage_audit_report.json"
ORIGINAL_BATCH_DIRS = [
    ROOT / "output/math_captain_msg_20260807_104217",
    ROOT / "output/math_captain_msg_20260806_174649",
    ROOT / "output/math_captain_msg_20260806_105150",
    ROOT / "output/math_captain_msg_20260805_181901",
    ROOT / "output/math_captain_msg_20260804_181219",
    ROOT / "output/math_captain_msg_20260804_110919",
]
DATA_SOURCE = "math"


def _safe_path_component(value: str) -> str:
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


def _apply_prompt_version(version: str) -> None:
    """Monkeypatch prompt constants before pipeline modules use them."""
    import utils.prompts as prompts

    if version == "v0":
        return
    if version != "v1":
        raise ValueError(f"Unsupported prompt version: {version!r} (use v0 or v1)")

    from utils.prompt_v1 import ATTACK_ANALYSIS_PROMPT_V1, REPLAY_PROMPT_V1

    prompts.ATTACK_ANALYSIS_PROMPT = ATTACK_ANALYSIS_PROMPT_V1
    prompts.REPLAY_PROMPT = REPLAY_PROMPT_V1

    import pipeline.coding.attack as attack_mod
    import monitor.attack_monitor as monitor_mod

    attack_mod.ATTACK_ANALYSIS_PROMPT = ATTACK_ANALYSIS_PROMPT_V1
    monitor_mod.REPLAY_PROMPT = REPLAY_PROMPT_V1


def _load_leaked_tasks(
    audit_report: Path,
    *,
    task_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    report = json.loads(audit_report.read_text(encoding="utf-8"))
    leaked = report.get("leaked_attack_tasks") or []
    if task_ids:
        leaked = [item for item in leaked if item["task_id"] in task_ids]
    return leaked


def _resolve_leak_audit_report(explicit: Path | None) -> Path:
    """Pick a leak-task report, rescanning original batches if the list is empty."""
    from utils.meta_leakage import audit_output_dirs, summarize_audit
    from dataclasses import asdict

    candidates = [explicit] if explicit else [DEFAULT_AUDIT_REPORT, FALLBACK_AUDIT_REPORT]
    for path in candidates:
        if not path.exists():
            continue
        leaked = _load_leaked_tasks(path)
        if leaked:
            print(f"Using leak audit report: {path} ({len(leaked)} tasks)")
            return path

    print(
        "Leak task list empty or report missing; rescanning original 6 batches...",
        file=sys.stderr,
    )
    valid_dirs = [d for d in ORIGINAL_BATCH_DIRS if (d / "final_results").is_dir()]
    if not valid_dirs:
        raise FileNotFoundError("Cannot rescan: original batch final_results not found")

    results = audit_output_dirs(valid_dirs)
    leaked_attacks = []
    for r in results:
        if r.sample_type != "attack" or not r.history_leak:
            continue
        item = asdict(r)
        item["history_hit_details"] = [asdict(h) for h in r.history_hit_details]
        leaked_attacks.append(item)

    report = {
        "summary": summarize_audit(results),
        "leaked_attack_tasks": leaked_attacks,
    }
    DEFAULT_AUDIT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_AUDIT_REPORT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Regenerated leak audit report: {DEFAULT_AUDIT_REPORT} ({len(leaked_attacks)} tasks)")
    return DEFAULT_AUDIT_REPORT


def _source_round0_dir(source_run: str, task_id: str) -> Path:
    return ROOT / "output" / source_run / DATA_SOURCE / "round_0" / task_id


def seed_round_0(
    leaked_tasks: list[dict[str, Any]],
    output_root: Path,
) -> dict[str, bool]:
    """Copy round_0 task dirs and build merged eval maps. Returns eval status."""
    round0_dir = output_root / DATA_SOURCE / "round_0"
    round0_dir.mkdir(parents=True, exist_ok=True)

    status: dict[str, bool] = {}
    messages: dict[str, str] = {}

    for item in leaked_tasks:
        task_id = item["task_id"]
        source_run = item["source_run"]
        src_task = _source_round0_dir(source_run, task_id)
        if not src_task.is_dir():
            raise FileNotFoundError(f"Missing round_0 source for {task_id}: {src_task}")

        dst_task = round0_dir / task_id
        if dst_task.exists():
            shutil.rmtree(dst_task)
        shutil.copytree(src_task, dst_task)

        src_eval = ROOT / "output" / source_run / DATA_SOURCE / "round_0" / f"eval_{DATA_SOURCE}.json"
        src_msg = ROOT / "output" / source_run / DATA_SOURCE / "round_0" / f"eval_msg_{DATA_SOURCE}.json"
        if src_eval.exists():
            src_status = json.loads(src_eval.read_text(encoding="utf-8"))
            status[task_id] = bool(src_status.get(task_id, False))
        else:
            status[task_id] = True
        if src_msg.exists():
            src_messages = json.loads(src_msg.read_text(encoding="utf-8"))
            messages[task_id] = str(src_messages.get(task_id, ""))

    eval_path = round0_dir / f"eval_{DATA_SOURCE}.json"
    msg_path = round0_dir / f"eval_msg_{DATA_SOURCE}.json"
    eval_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    msg_path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
    return status


def _log_to_task(log: dict[str, Any]) -> dict[str, Any]:
    """Build a minimal task row from a round log for runner compatibility."""
    task_id = log.get("question_ID") or log.get("task_id")
    return {
        "task_id": task_id,
        "question": log.get("question", ""),
        "reference_solution": log.get("ground_truth", ""),
        "ground_truth": log.get("ground_truth", ""),
        "test": log.get("test", ""),
        "data_source": DATA_SOURCE,
    }


async def _rerun_attack_rounds(
    leaked_tasks: list[dict[str, Any]],
    *,
    output_root: Path,
    workspace_root: Path,
    backend_name: str,
    env_file: Path | None,
    max_rounds: int,
    skip_existing: bool,
    concurrency: int,
) -> None:
    from adapter.runtime_bootstrap import bootstrap_backend_runtime
    from monitor.attack_monitor import AttackMonitor
    from pipeline.coding.attack import attack_analysis, get_attack_analysis
    from pipeline.eval.core import evaluate_round, load_eval_results
    from pipeline.runners.text_answer import run_text_answer_task
    from utils.common import read_json_file, save_final_result, write_json_file
    from utils.logging import logger

    bootstrap_backend_runtime(backend_name, env_file)
    backend_module = importlib.import_module(f"adapter.{backend_name}.core")
    backend = getattr(backend_module, f"{backend_name}Adapter")()

    task_ids = [item["task_id"] for item in leaked_tasks]
    eval_path = output_root / DATA_SOURCE / "round_0"
    eval_results, msg_results = load_eval_results(eval_path, DATA_SOURCE)

    semaphore = Semaphore(max(concurrency, 1))
    completed: set[str] = set()

    for current in range(1, max_rounds + 1):
        last_eval_results = eval_results

        async def _process_one(task_key: str) -> None:
            task_id = _safe_path_component(task_key)
            if task_id in completed:
                return

            last_round_output = output_root / DATA_SOURCE / f"round_{current - 1}" / task_id
            if not last_round_output.exists():
                logger.error("Missing round_%d output for %s", current - 1, task_id)
                return

            if not last_eval_results.get(task_key, False):
                logger.info("Task %s was not success in round %d; skip attack rerun", task_id, current - 1)
                return

            last_round_log = read_json_file(last_round_output / "log.json")
            last_round_log = ast.literal_eval(
                str(last_round_log).replace(f"round_{current - 1}", f"round_{current}")
            )

            output = output_root / DATA_SOURCE / f"round_{current}" / task_id
            output.mkdir(parents=True, exist_ok=True)
            if (output / "log.json").exists() and skip_existing:
                logger.info("Round %d log exists for %s, skipping", current, task_id)
                return

            workspace = workspace_root / DATA_SOURCE / f"round_{current}" / f"{task_id}_attack_analysis"
            workspace.mkdir(parents=True, exist_ok=True)
            previous_injections_path = last_round_output / "attack_analysis.json"
            previous_injections = (
                read_json_file(previous_injections_path)
                if previous_injections_path.exists()
                else []
            )

            is_success = await attack_analysis(
                task=last_round_log,
                workspace=workspace,
                output=output,
                backend=backend,
                skipping_exists=False,
                injection_history=previous_injections,
                message=msg_results.get(task_key, ""),
                semaphore=semaphore,
            )
            if not is_success:
                logger.info("Attack analysis failed for %s round %d; copying prev round", task_id, current)
                shutil.copytree(last_round_output, output, dirs_exist_ok=True)
                return

            replay_info = get_attack_analysis(output)
            replay_recovery_path = last_round_output / "recovery"
            monitor = AttackMonitor(
                replay_recovery_path,
                workspace,
                backend,
                replay_info[-1],
                last_round_log,
            )
            task_row = _log_to_task(last_round_log)
            result = run_text_answer_task(
                task_row,
                workspace,
                output,
                backend,
                skip_existing=False,
                monitor=monitor,
                semaphore=semaphore,
            )
            if inspect.isawaitable(result):
                result = await result
            if not result:
                logger.info("Replay failed for %s round %d; copying prev round", task_id, current)
                shutil.copytree(last_round_output, output, dirs_exist_ok=True)

        await asyncio.gather(*(_process_one(tid) for tid in task_ids))

        eval_results, msg_results = await evaluate_round(
            output_root / DATA_SOURCE / f"round_{current}",
            data_source=DATA_SOURCE,
            backend=backend_name,
            semaphore=semaphore,
            skip_existing=skip_existing,
        )

        for task_key in eval_results:
            if task_key not in last_eval_results:
                continue
            if eval_results[task_key] ^ last_eval_results[task_key]:
                task_id = _safe_path_component(task_key)
                output = output_root / DATA_SOURCE / f"round_{current}" / task_id
                prev_output = output_root / DATA_SOURCE / f"round_{current - 1}" / task_id
                try:
                    final_info = get_attack_analysis(output)
                    analysis_source_log = read_json_file(prev_output / "log.json")
                    save_final_result(output_root / "final_results", analysis_source_log, final_info)
                    completed.add(task_id)
                    logger.info("Final result saved for %s (attack flip)", task_id)
                except Exception as exc:
                    logger.error("Finalization failed for %s: %s", task_id, exc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Rerun leaked attack samples from seeded round_0")
    parser.add_argument(
        "--audit-report",
        type=Path,
        default=None,
        help=(
            "JSON report with leaked_attack_tasks "
            "(default: output/meta_leakage_audit_report_original_batches.json; "
            "auto-rescan original 6 batches if empty)"
        ),
    )
    parser.add_argument(
        "--task-ids",
        type=str,
        default="",
        help="Comma-separated subset of task IDs to rerun (default: all leaked)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "output" / "math_captain_prompt_v1_rerun",
        help="New output root for rerun artifacts",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        default=ROOT / "workspace" / "math_captain_prompt_v1_rerun",
        help="Workspace root for attack analysis",
    )
    parser.add_argument("--backend", type=str, default="Captain")
    parser.add_argument("--env_file", type=Path, default=None)
    parser.add_argument("--max-rounds", type=int, default=3)
    parser.add_argument("--concurrent", type=int, default=1)
    parser.add_argument(
        "--prompt-version",
        type=str,
        choices=["v0", "v1"],
        default="v1",
        help="Prompt set to use (default: v1)",
    )
    parser.add_argument("-s", "--skip-existing", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List tasks that would be rerun without executing",
    )
    args = parser.parse_args()

    if not args.audit_report or not args.audit_report.exists():
        audit_path = _resolve_leak_audit_report(args.audit_report)
    else:
        audit_path = args.audit_report
        leaked_probe = _load_leaked_tasks(audit_path)
        if not leaked_probe:
            print(
                f"Warning: {audit_path} has empty leaked_attack_tasks; rescanning original batches.",
                file=sys.stderr,
            )
            audit_path = _resolve_leak_audit_report(None)

    only_ids = {x.strip() for x in args.task_ids.split(",") if x.strip()} or None
    leaked_tasks = _load_leaked_tasks(audit_path, task_ids=only_ids)
    if not leaked_tasks:
        print("No leaked attack tasks matched.", file=sys.stderr)
        sys.exit(1)

    print(f"Leaked attack tasks to rerun: {len(leaked_tasks)}")
    by_run: dict[str, int] = {}
    for item in leaked_tasks:
        by_run[item["source_run"]] = by_run.get(item["source_run"], 0) + 1
    for run, count in sorted(by_run.items()):
        print(f"  {run}: {count}")
    print(f"Prompt version: {args.prompt_version}")
    print(f"Output: {args.output.resolve()}")

    if args.dry_run:
        print("\nTask list:")
        for item in leaked_tasks:
            markers = ",".join(item.get("history_hits") or [])
            ac_note = " (ac meta too)" if item.get("attacked_content_leak") else ""
            print(f"  {item['task_id']}  [{markers}]{ac_note}  <- {item['source_run']}")
        return

    output_root = args.output.resolve()
    workspace_root = args.workspace.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "final_results").mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)

    _apply_prompt_version(args.prompt_version)
    print("\nSeeding round_0 from source runs...")
    status = seed_round_0(leaked_tasks, output_root)
    failed_seed = [tid for tid, ok in status.items() if not ok]
    if failed_seed:
        print(f"Warning: {len(failed_seed)} tasks were not eval-pass in source round_0: {failed_seed[:5]}...")

    print("Starting attack rerun rounds...")
    asyncio.run(
        _rerun_attack_rounds(
            leaked_tasks,
            output_root=output_root,
            workspace_root=workspace_root,
            backend_name=args.backend,
            env_file=args.env_file,
            max_rounds=args.max_rounds,
            skip_existing=args.skip_existing,
            concurrency=args.concurrent,
        )
    )
    print(f"\nDone. Results under: {output_root}")


if __name__ == "__main__":
    main()
