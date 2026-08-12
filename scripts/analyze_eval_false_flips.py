#!/usr/bin/env python3
"""Find eval-induced false flips in final_results and verify new eval fixes them."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.math.equivalence import is_equiv, strip_string  # noqa: E402

_BOXED_PREFIX = r"\boxed{"
_OUTER_BRACE_RE = re.compile(r"^\{([^{}]+)\}$")
_NUMERIC_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")
_MAX_OUTER_BRACE_PEELS = 2


def extract_boxed_answer(text: str) -> str:
    if not text:
        return ""
    start = text.rfind(_BOXED_PREFIX)
    if start == -1:
        return ""
    i = start + len(_BOXED_PREFIX)
    depth = 1
    while i < len(text):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + len(_BOXED_PREFIX) : i].strip()
        i += 1
    return ""


def normalize_boxed_answer(raw: str) -> str:
    if not raw:
        return ""
    value = raw.strip()
    for _ in range(_MAX_OUTER_BRACE_PEELS):
        match = _OUTER_BRACE_RE.match(value)
        if not match:
            break
        value = match.group(1).strip()
    if _NUMERIC_RE.fullmatch(value):
        try:
            number = float(value)
        except (ValueError, OverflowError):
            return value
        if abs(number - round(number)) < 1e-9:
            return str(int(round(number)))
    return value


def old_eval(solution: str, ground_truth: str) -> bool:
    truth = normalize_boxed_answer(extract_boxed_answer(ground_truth))
    if not truth:
        return False
    answer = normalize_boxed_answer(extract_boxed_answer(solution))
    return answer == truth


def new_eval(solution: str, ground_truth: str) -> bool:
    truth = normalize_boxed_answer(extract_boxed_answer(ground_truth))
    if not truth:
        return False
    answer = normalize_boxed_answer(extract_boxed_answer(solution))
    return is_equiv(strip_string(answer), strip_string(truth))


def load_eval_round(run_root: Path, rnd: int) -> dict[str, bool]:
    path = run_root / "math" / f"round_{rnd}" / "eval_math.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def load_task_log(run_root: Path, rnd: int, task_id: str) -> dict | None:
    path = run_root / "math" / f"round_{rnd}" / task_id / "log.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def find_flip_round(run_root: Path, task_id: str, max_round: int = 3) -> tuple[int, str] | None:
    """Return (flip_round, flip_type) where flip_type is attack or diagnose."""

    prev = None
    for rnd in range(max_round + 1):
        evals = load_eval_round(run_root, rnd)
        if task_id not in evals:
            continue
        cur = evals[task_id]
        if prev is not None and cur != prev[1]:
            flip_type = "attack" if prev[1] and not cur else "diagnose"
            return rnd, flip_type
        prev = (rnd, cur)
    return None


def analyze_run(run_tag: str) -> list[dict]:
    run_root = ROOT / "output" / run_tag
    final_dir = run_root / "final_results"
    rows: list[dict] = []

    for final_path in sorted(final_dir.glob("task_*.json")):
        task_id = final_path.stem
        flip_info = find_flip_round(run_root, task_id)
        if flip_info is None:
            continue
        flip_round, flip_type = flip_info
        prev_round = flip_round - 1

        prev_log = load_task_log(run_root, prev_round, task_id)
        flip_log = load_task_log(run_root, flip_round, task_id)
        if not prev_log or not flip_log:
            continue

        prev_old = old_eval(prev_log["model_prediction"], prev_log["ground_truth"])
        prev_new = new_eval(prev_log["model_prediction"], prev_log["ground_truth"])
        flip_old = old_eval(flip_log["model_prediction"], flip_log["ground_truth"])
        flip_new = new_eval(flip_log["model_prediction"], flip_log["ground_truth"])

        stored_prev = load_eval_round(run_root, prev_round).get(task_id)
        stored_flip = load_eval_round(run_root, flip_round).get(task_id)

        pred_box = normalize_boxed_answer(extract_boxed_answer(flip_log["model_prediction"]))
        gt_box = normalize_boxed_answer(extract_boxed_answer(flip_log["ground_truth"]))

        # Eval-induced false flip: stored flip exists, but new eval shows no flip.
        old_flip = stored_prev is not None and stored_flip is not None and stored_prev != stored_flip
        new_would_flip = prev_new != flip_new

        eval_false_flip = old_flip and not new_would_flip

        # Also flag: flip round semantically correct under new eval but old eval failed.
        flip_round_correct_under_new = flip_new and not flip_old

        rows.append(
            {
                "run": run_tag,
                "task_id": task_id,
                "flip_type": flip_type,
                "flip_round": flip_round,
                "stored_prev": stored_prev,
                "stored_flip": stored_flip,
                "prev_old": prev_old,
                "prev_new": prev_new,
                "flip_old": flip_old,
                "flip_new": flip_new,
                "pred_boxed": pred_box,
                "gt_boxed": gt_box,
                "eval_false_flip": eval_false_flip,
                "flip_round_correct_under_new": flip_round_correct_under_new,
            }
        )
    return rows


def main() -> None:
    runs = [
        "math_captain_msg_20260804_110919",
        "math_captain_msg_20260804_181219",
    ]
    all_rows: list[dict] = []
    for run in runs:
        all_rows.extend(analyze_run(run))

    false_flips = [r for r in all_rows if r["eval_false_flip"]]
    partial = [
        r
        for r in all_rows
        if not r["eval_false_flip"] and r["flip_round_correct_under_new"]
    ]

    print("=" * 100)
    print(f"Total final_results with detectable flip: {len(all_rows)}")
    print(f"Eval-induced false flips (old flip, new eval no flip): {len(false_flips)}")
    print(f"Flip round correct under new eval only (old fail/new pass): {len(partial)}")
    print("=" * 100)

    if false_flips:
        print("\n## Eval-induced false flips — new eval removes the flip\n")
        for r in false_flips:
            print(
                f"- {r['run']} | {r['task_id']} | {r['flip_type']} @ round {r['flip_round']} | "
                f"stored {r['stored_prev']}->{r['stored_flip']} | "
                f"old {r['prev_old']}->{r['flip_old']} | new {r['prev_new']}->{r['flip_new']} | "
                f"pred={r['pred_boxed']!r} gt={r['gt_boxed']!r}"
            )

    if partial:
        print("\n## Flip round: old fail but new pass (may still flip if prev round differs)\n")
        for r in partial:
            if r in false_flips:
                continue
            print(
                f"- {r['run']} | {r['task_id']} | {r['flip_type']} @ round {r['flip_round']} | "
                f"old flip {r['prev_old']}->{r['flip_old']} | new {r['prev_new']}->{r['flip_new']} | "
                f"pred={r['pred_boxed']!r} gt={r['gt_boxed']!r}"
            )

    true_semantic = [
        r
        for r in all_rows
        if not r["eval_false_flip"] and r["flip_type"] == "attack" and r["flip_new"] is False
    ]
    print(f"\n## Semantic attack flips (new eval still fails at flip round): {len(true_semantic)}")
    for r in true_semantic:
        print(
            f"- {r['run']} | {r['task_id']} @ round {r['flip_round']} | "
            f"pred={r['pred_boxed']!r} gt={r['gt_boxed']!r}"
        )

    diagnose = [r for r in all_rows if r["flip_type"] == "diagnose"]
    print(f"\n## Diagnose flips (fail->pass): {len(diagnose)}")
    diag_false = [r for r in diagnose if r["eval_false_flip"]]
    print(f"   Eval-induced false diagnose flips: {len(diag_false)}")
    for r in diagnose:
        mark = " [EVAL FALSE]" if r["eval_false_flip"] else ""
        print(
            f"- {r['run']} | {r['task_id']} @ round {r['flip_round']}{mark} | "
            f"old {r['prev_old']}->{r['flip_old']} | new {r['prev_new']}->{r['flip_new']} | "
            f"pred={r['pred_boxed']!r} gt={r['gt_boxed']!r}"
        )


if __name__ == "__main__":
    main()
