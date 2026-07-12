"""Shared serialization and result-shaping helpers used across the project."""

from functools import partial
import json
from pathlib import Path
from typing import Any, Iterable

from pydantic import BaseModel
from pydantic_core import to_jsonable_python


def dumps(model: BaseModel | Iterable[BaseModel]):
    """Convert one or many Pydantic models into JSON-serializable Python objects."""
    if isinstance(model, BaseModel):
        return model.model_dump()
    else:
        return [m.model_dump() for m in model]

def read_json_file(json_file: Path, encoding: str = "utf-8"):
    """Read and parse a JSON file with basic error handling."""
    if not json_file.exists():
        raise FileNotFoundError(f"json_file: {json_file} not exist")

    with open(json_file, "r", encoding=encoding) as fin:
        try:
            data = json.load(fin)
        except Exception:
            raise ValueError(f"read json file: {json_file} failed")
    return data

def write_json_file(json_file: Path, data: Any, encoding: str = "utf-8", indent: int = 4, use_fallback: bool = False):
    """Write JSON data to disk and create parent directories when missing."""
    folder_path = json_file.parent
    if not folder_path.exists():
        folder_path.mkdir(parents=True, exist_ok=True)

    custom_default = to_jsonable_python

    with open(json_file, "w", encoding=encoding) as fout:
        json.dump(data, fout, ensure_ascii=False, indent=indent, default=custom_default)

def match_info(attack_info: list, diagnose_info: list):
    """Compare two attribution chains by step/fault pairs."""
    attack_info = [(info['step_id'], info['fault_code']) for info in attack_info]
    diagnose_info = [(info['step_id'], info['fault_code']) for info in diagnose_info]
    return attack_info == diagnose_info


def _truncate_middle(text: str, limit: int) -> str:
    """Keep the beginning and end of long trace text within a hard character cap."""
    if len(text) <= limit:
        return text
    head = max(limit // 2, 0)
    tail = max(limit - head, 0)
    return (
        text[:head]
        + f"\n...[truncated {len(text) - limit} chars for attribution prompt size]...\n"
        + text[-tail:]
    )


def compact_history_for_prompt(
    history: list[dict],
    max_step_chars: int = 1800,
    max_total_chars: int = 50000,
) -> str:
    """Serialize execution history for attribution prompts without dropping step ids."""
    compacted = []
    for item in history:
        if isinstance(item, dict):
            new_item = dict(item)
            content = "" if new_item.get("content") is None else str(new_item.get("content"))
            new_item["content"] = _truncate_middle(content, max_step_chars)
            compacted.append(new_item)
        else:
            compacted.append(_truncate_middle(str(item), max_step_chars))

    rendered = json.dumps(compacted, ensure_ascii=False, indent=2, default=to_jsonable_python)
    if len(rendered) <= max_total_chars:
        return rendered

    per_step_limit = max(400, max_total_chars // max(len(compacted), 1))
    smaller = []
    for item in history:
        if isinstance(item, dict):
            new_item = dict(item)
            content = "" if new_item.get("content") is None else str(new_item.get("content"))
            new_item["content"] = _truncate_middle(content, per_step_limit)
            smaller.append(new_item)
        else:
            smaller.append(_truncate_middle(str(item), per_step_limit))
    rendered = json.dumps(smaller, ensure_ascii=False, indent=2, default=to_jsonable_python)
    return _truncate_middle(rendered, max_total_chars)


def validate_attribution_info(log: dict, info: list[dict]):
    """Validate that attribution steps point to real history entries."""
    history_len = len(log.get('history', []))
    task_id = log.get('question_ID', '<unknown>')
    for idx, item in enumerate(info):
        step = item.get('step_id')
        if not isinstance(step, int) or step < 1 or step > history_len:
            raise ValueError(
                f"Invalid step_id for task {task_id}: info[{idx}].step_id={step} "
                f"but history has {history_len} steps"
            )
        related_error = item.get('related_error')
        if not isinstance(related_error, list):
            raise ValueError(
                f"Invalid related_error for task {task_id}: "
                f"info[{idx}].related_error must be a list"
            )
        for related_step in related_error:
            if (
                not isinstance(related_step, int)
                or related_step < 1
                or related_step > history_len
            ):
                raise ValueError(
                    f"Invalid related_error for task {task_id}: "
                    f"info[{idx}].related_error contains {related_step} "
                    f"but history has {history_len} steps"
                )

def save_final_result(path: Path, log: dict, info: list):
    """Assemble and persist final attribution result for one task."""
    validate_attribution_info(log, info)
    id = log['question_ID']
    log['mistake_information'] = info
    log['attribution_subgraph'] = {
        'nodes': [],
        'edges': {}
    }
    for i in info:
        step = i['step_id']
        i['mistake_agent'] = log['history'][step-1]['name']
        if step not in log['attribution_subgraph']['nodes']:
            log['attribution_subgraph']['nodes'].append(step)
        for e in  i['related_error']:
            edges = log['attribution_subgraph']['edges'].setdefault(step, [])
            if e not in log['attribution_subgraph']['nodes']:
                log['attribution_subgraph']['nodes'].append(e)            
            if e not in edges:
                edges.append(e)

    write_json_file(path / f'{id}.json', log)
    
