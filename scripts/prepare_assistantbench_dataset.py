"""Convert AssistantBench JSONL files into the parquet schema used by main.py."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        default="dataset/assistantbench/assistant_bench_v1.0_dev.jsonl",
        help="AssistantBench JSONL input file.",
    )
    parser.add_argument(
        "--output",
        default="dataset/assistantbench/assistantbench_dev.parquet",
        help="Output parquet file consumed by main.py.",
    )
    parser.add_argument("--max-samples", type=int, default=None)
    args = parser.parse_args()

    src = Path(args.input)
    out = Path(args.output)
    raw_rows = _read_jsonl(src)
    if args.max_samples is not None:
        raw_rows = raw_rows[: args.max_samples]

    rows = []
    for idx, row in enumerate(raw_rows):
        task_id = str(row.get("id") or f"assistantbench_{idx:04d}")
        rows.append(
            {
                "task_id": task_id,
                "question_ID": task_id,
                "question": str(row.get("task", "")),
                "reference_solution": "" if row.get("answer") is None else str(row.get("answer")),
                "test": "",
                "data_source": "assistantbench",
                "file_name": "",
                "gold_url": "" if row.get("gold_url") is None else str(row.get("gold_url")),
                "explanation": "" if row.get("explanation") is None else str(row.get("explanation")),
                "difficulty": "" if row.get("difficulty") is None else str(row.get("difficulty")),
                "set": "" if row.get("set") is None else str(row.get("set")),
            }
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(out, index=False)
    print(f"wrote {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
