"""Shared serialization and result-shaping helpers used across the project."""

import asyncio
from functools import partial
import json
import os
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


def load_openai_compatible_env() -> tuple[str, str, str, float]:
    """Load direct analysis LLM config from explicit ``ANALYSIS_*`` env vars."""
    api_key = (os.getenv("ANALYSIS_API_KEY") or "").strip()
    base_url = (os.getenv("ANALYSIS_BASE_URL") or "").strip()
    model_name = (os.getenv("ANALYSIS_MODEL") or "").strip()
    temperature_raw = (os.getenv("ANALYSIS_TEMPERATURE") or "0").strip()

    missing: list[str] = []
    if not api_key:
        missing.append("ANALYSIS_API_KEY")
    if not base_url:
        missing.append("ANALYSIS_BASE_URL")
    if not model_name:
        missing.append("ANALYSIS_MODEL")
    if missing:
        raise RuntimeError("Missing OpenAI-compatible runtime config: " + ", ".join(missing))

    try:
        temperature = float(temperature_raw)
    except ValueError:
        temperature = 0.0

    return api_key, base_url, model_name, temperature


async def run_llm_completion(
    prompt: str,
    *,
    system_prompt: str | None = None,
) -> str:
    """Run one plain OpenAI-compatible chat completion and return the text content."""
    api_key, base_url, model_name, temperature = load_openai_compatible_env()

    def _run() -> str:
        from openai import OpenAI

        client_kwargs = {"api_key": api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        max_retries_raw = (os.getenv("ANALYSIS_MAX_RETRIES") or os.getenv("LLM_MAX_RETRIES") or "0").strip()
        if max_retries_raw.isdigit():
            client_kwargs["max_retries"] = int(max_retries_raw)
        client = OpenAI(**client_kwargs)

        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        completion = client.chat.completions.create(
            model=model_name,
            messages=messages,
            temperature=temperature,
        )
        return completion.choices[0].message.content or ""

    return await asyncio.to_thread(_run)


def extract_json_object_from_model_output(
    result: object,
    required_keys: set[str] | None = None,
    require_step_id_int: bool = True,
) -> dict | None:
    """Try to parse and return one JSON object from a model output payload."""
    if result is None:
        return None

    keys = required_keys or set()

    def _normalize(payload: object) -> dict | None:
        if not isinstance(payload, dict):
            return None
        if keys and not keys.issubset(payload):
            return None
        if not require_step_id_int:
            return payload

        step_id = payload.get("step_id")
        if isinstance(step_id, str):
            step_id = step_id.strip()
            if step_id.isdigit():
                payload = dict(payload)
                payload["step_id"] = int(step_id)
                step_id = payload["step_id"]
        return payload if isinstance(step_id, int) else None

    direct = _normalize(result)
    if direct is not None:
        return direct

    if isinstance(result, list):
        for item in reversed(result):
            direct = _normalize(item)
            if direct is not None:
                return direct

    text = str(result)
    if not text:
        return None

    decoder = json.JSONDecoder()
    idx = 0
    last_valid = None
    while idx < len(text):
        start = text.find("{", idx)
        if start < 0:
            break
        try:
            payload, end = decoder.raw_decode(text, start)
        except Exception:
            idx = start + 1
            continue

        valid = _normalize(payload)
        if valid is not None:
            last_valid = valid
        idx = end

    return last_valid

def match_info(attack_info: list, diagnose_info: list):
    """Compare two attribution chains by step/fault pairs."""
    attack_info = [(info['step_id'], info['fault_code']) for info in attack_info]
    diagnose_info = [(info['step_id'], info['fault_code']) for info in diagnose_info]
    return attack_info == diagnose_info


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

def save_final_result(
    path: Path, log: dict, info: list, *, agent_source_log: dict | None = None
):
    """Assemble and persist final attribution result for one task."""
    validate_attribution_info(log, info)
    agent_log = agent_source_log if agent_source_log is not None else log
    validate_attribution_info(agent_log, info)
    id = log['question_ID']
    log['mistake_information'] = info
    log['attribution_subgraph'] = {
        'nodes': [],
        'edges': {}
    }
    for i in info:
        step = i['step_id']
        i['mistake_agent'] = agent_log['history'][step - 1]['name']
        if step not in log['attribution_subgraph']['nodes']:
            log['attribution_subgraph']['nodes'].append(step)
        for e in  i['related_error']:
            edges = log['attribution_subgraph']['edges'].setdefault(step, [])
            if e not in log['attribution_subgraph']['nodes']:
                log['attribution_subgraph']['nodes'].append(e)            
            if e not in edges:
                edges.append(e)

    write_json_file(path / f'{id}.json', log)
    
