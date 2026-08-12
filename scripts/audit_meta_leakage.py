#!/usr/bin/env python3
"""Audit final_results for meta-language leakage in attack attribution samples.

Usage:
    python scripts/audit_meta_leakage.py \\
        output/math_captain_msg_20260807_104217 \\
        output/math_captain_msg_20260806_174649

    python scripts/audit_meta_leakage.py --glob 'output/math_captain_msg_*'

Outputs a JSON report (default: stdout) with per-sample details and summary stats.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.meta_leakage import audit_output_dirs, summarize_audit  # noqa: E402


DEFAULT_RUN_DIRS = [
    "output/math_captain_msg_20260807_104217",
    "output/math_captain_msg_20260806_174649",
    "output/math_captain_msg_20260806_105150",
    "output/math_captain_msg_20260805_181901",
    "output/math_captain_msg_20260804_181219",
    "output/math_captain_msg_20260804_110919",
]


def _resolve_dirs(args: argparse.Namespace) -> list[Path]:
    if args.glob:
        return sorted(ROOT.glob(args.glob))
    dirs = [Path(p) for p in (args.runs or DEFAULT_RUN_DIRS)]
    return [d if d.is_absolute() else ROOT / d for d in dirs]


def _print_summary(summary: dict) -> None:
    print("=" * 60)
    print("Meta-language leakage audit summary")
    print("=" * 60)
    print("Leak definition: history[].content injection-meta only")
    print("(attacked_content meta alone is NOT counted as leaked)")
    print()
    print(f"Total final_results : {summary['total_final_results']}")
    print(f"  Attack            : {summary['attack_count']}")
    print(f"  Diagnose          : {summary['diagnose_count']}")
    print()
    print(
        f"Leaked Attack samples: {summary['leaked_attack_count']} / {summary['attack_count']} "
        f"({summary['leaked_attack_rate']:.1%})"
    )
    print(
        f"  attacked_content-only (not counted): "
        f"{summary['attacked_content_only_not_counted']}"
    )
    print()
    print("Per-run breakdown:")
    for run, stats in sorted(summary["by_run"].items()):
        rate = stats["leaked"] / stats["attack"] if stats["attack"] else 0
        print(
            f"  {run}: total={stats['total']} attack={stats['attack']} "
            f"diagnose={stats['diagnose']} history_leaked={stats['leaked']} "
            f"ac_only={stats.get('ac_only', 0)} ({rate:.1%})"
        )
    print()
    if summary["top_history_markers"]:
        print("Top history injection markers:")
        for name, count in summary["top_history_markers"][:12]:
            print(f"  {name}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit meta-language leakage in final_results")
    parser.add_argument(
        "runs",
        nargs="*",
        help="Output run directories (each containing final_results/). Defaults to the six math_captain_msg batches.",
    )
    parser.add_argument(
        "--glob",
        type=str,
        default="",
        help="Glob pattern under repo root instead of explicit run dirs (e.g. 'output/math_captain_msg_*')",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Write full JSON report to this path (still prints summary to stdout)",
    )
    parser.add_argument(
        "--leaked-only",
        action="store_true",
        help="In JSON output, include only leaked attack samples",
    )
    args = parser.parse_args()

    run_dirs = _resolve_dirs(args)
    missing = [str(d) for d in run_dirs if not (d / "final_results").is_dir()]
    if missing:
        print(f"Warning: no final_results/ in: {', '.join(missing)}", file=sys.stderr)

    valid_dirs = [d for d in run_dirs if (d / "final_results").is_dir()]
    if not valid_dirs:
        print("Error: no valid run directories found.", file=sys.stderr)
        sys.exit(1)

    results = audit_output_dirs(valid_dirs)
    summary = summarize_audit(results)

    leaked_attacks = []
    for r in results:
        if r.sample_type != "attack" or not r.history_leak:
            continue
        item = asdict(r)
        item["history_hit_details"] = [asdict(h) for h in r.history_hit_details]
        leaked_attacks.append(item)
    all_samples = [asdict(r) for r in results]

    report = {
        "summary": summary,
        "leaked_attack_tasks": leaked_attacks,
        "all_samples": all_samples if not args.leaked_only else None,
    }
    if args.leaked_only:
        del report["all_samples"]

    _print_summary(summary)

    out_path = args.output
    if out_path is None:
        if args.runs or args.glob:
            if len(valid_dirs) == 1:
                out_path = ROOT / "output" / f"meta_leakage_audit_{valid_dirs[0].name}.json"
            else:
                out_path = ROOT / "output" / "meta_leakage_audit_report.json"
        else:
            out_path = ROOT / "output" / "meta_leakage_audit_report_original_batches.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"Full report written to: {out_path}")
    print(f"Leaked attack task IDs ({len(leaked_attacks)}):")
    for item in leaked_attacks:
        markers = ",".join(item.get("history_hits") or [])
        print(f"  {item['task_id']}  [{markers}]  ({item['source_run']})")


if __name__ == "__main__":
    main()
