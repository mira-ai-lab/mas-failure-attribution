"""Helpers for reading task logs and writing eval artifacts."""

from __future__ import annotations

import json
from pathlib import Path

from pipeline.eval.types import EvalBatchResult


def _read_json_file(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def eval_result_path(eval_path: Path, data_source: str) -> Path:
    """Return the round-level status file path for one dataset."""

    return eval_path / f"eval_{data_source}.json"


def eval_message_path(eval_path: Path, data_source: str) -> Path:
    """Return the round-level message file path for one dataset."""

    return eval_path / f"eval_msg_{data_source}.json"


def load_round_tasks(eval_path: Path) -> list[dict]:
    """Load all per-task ``log.json`` files under one round directory."""

    eval_log_paths = sorted(p / "log.json" for p in eval_path.iterdir() if p.is_dir())
    return [_read_json_file(path) for path in eval_log_paths if path.exists()]


def write_eval_artifacts(eval_path: Path, data_source: str, result: EvalBatchResult) -> None:
    """Persist status and message maps for one dataset round."""

    save_path = eval_result_path(eval_path, data_source)
    msg_path = eval_message_path(eval_path, data_source)
    save_path.parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(result.status, f, ensure_ascii=False, indent=2)
    with open(msg_path, "w", encoding="utf-8") as f:
        json.dump(result.messages, f, ensure_ascii=False, indent=2)


def load_eval_artifacts(eval_path: Path, data_source: str) -> EvalBatchResult:
    """Load persisted eval artifacts, tolerating older runs without msg files."""

    status = _read_json_file(eval_result_path(eval_path, data_source))
    msg_path = eval_message_path(eval_path, data_source)
    if msg_path.exists():
        messages = _read_json_file(msg_path)
    else:
        messages = {task_id: "" for task_id in status}
    return EvalBatchResult(status=status, messages=messages)
