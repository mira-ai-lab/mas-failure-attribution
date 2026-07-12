"""AutoGen MagenticOneGroupChat adapter (aligned with te_mg_2_parquet + BaseAdapter)."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from adapter.MagenticOne.event_dump import serialize_stream_event
from adapter.MagenticOne.magentic_runtime import (
    WINDOWS_TEAM_TASK_PREFIX,
    apply_built_topology_to_monitor,
    close_surfer_silently,
    create_magentic_team,
    env_flag,
    extract_model_prediction_from_task_result,
    gaia_attachments_root,
    gaia_preamble_for_pipeline_task,
    is_transient_run_stream_failure,
    patch_magentic_one_orchestrator_prompts_for_windows,
    build_agent_topology,
    build_system_prompt,
    system_prompt_map_to_strings,
)
from adapter.base_adapter import BaseAdapter
from monitor.attack_monitor import AttackMonitor
from monitor.base_monitor import BaseMonitor, RoleType
from utils.logging import logger

# Ensure Windows orchestrator patch applied when this module loads.
patch_magentic_one_orchestrator_prompts_for_windows()


def _build_model_client():
    from autogen_ext.models.openai import OpenAIChatCompletionClient

    model = os.environ.get("MAGENTIC_MODEL", "gpt-4o")
    api_key = os.environ.get("MAGENTIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set MAGENTIC_API_KEY or OPENAI_API_KEY for OpenAIChatCompletionClient."
        )
    base_url = os.environ.get("MAGENTIC_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
    kwargs: dict[str, Any] = {"model": model, "api_key": api_key}
    if base_url:
        kwargs["base_url"] = base_url
    
    # Load model_info from environment when provided.
    model_info = os.environ.get("MAGENTIC_MODEL_INFO_JSON")
    if model_info:
        try:
            kwargs["model_info"] = json.loads(model_info)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse MAGENTIC_MODEL_INFO_JSON: {e}")
    
    # Default model_info for non-standard / custom endpoints when unset.
    if "model_info" not in kwargs:
        # Detect non-canonical OpenAI model ids or OpenRouter-style hosts.
        is_openrouter = base_url and "openrouter" in base_url.lower()
        is_custom_model = model not in ["gpt-4o", "gpt-4", "gpt-3.5-turbo", "gpt-4-turbo"]
        
        if is_openrouter or is_custom_model:
            kwargs["model_info"] = {
                "family": model.split("/")[0] if "/" in model else "unknown",
                "vision": False,
                "function_calling": True,
                "json_output": True,
                "structured_output": True,
            }
            logger.info(f"Using default model_info for non-standard model: {model}")
    
    timeout = os.environ.get("MAGENTIC_TIMEOUT_SEC")
    if timeout and timeout.isdigit():
        kwargs["timeout"] = int(timeout)
    max_retries = os.environ.get("MAGENTIC_MAX_RETRIES")
    if max_retries and max_retries.isdigit():
        kwargs["max_retries"] = int(max_retries)
    
    # Custom HTTP client (SSL verify disabled) for restrictive proxies / dev setups.
    custom_http_client = httpx.AsyncClient(verify=False)
    kwargs["http_client"] = custom_http_client
    
    logger.info(f"Building model client with: model={model}, base_url={base_url}")
    if "model_info" in kwargs:
        logger.info(f"Using model_info: {kwargs['model_info']}")
    
    return OpenAIChatCompletionClient(**kwargs)


def _compose_task_text(idea: str, task: Optional[Dict[str, Any]]) -> str:
    parts = [WINDOWS_TEAM_TASK_PREFIX]
    if task is not None:
        root = os.environ.get("GAIA_ATTACHMENTS_ROOT", "").strip()
        if root:
            parts.append(gaia_preamble_for_pipeline_task(task, Path(root)))
        else:
            # Default GAIA raw tree under adapter/MagenticOne/dataset when unset.
            default_root = gaia_attachments_root(Path(__file__).resolve().parent)
            if default_root.is_dir():
                parts.append(gaia_preamble_for_pipeline_task(task, default_root))
    parts.append(idea)
    return "".join(parts)


class MagenticOneAdapter(BaseAdapter):
    """Run MagenticOneGroupChat with Windows-safe prompts, retries, and tracing."""

    def __init__(self) -> None:
        self._last_trace_path: Path | None = None
        self._model_label: str = os.environ.get("MAGENTIC_MODEL", "gpt-4o")
        self._cached_prompt_map: Dict[str, str] = {}
        self._task_result_prediction: str = ""

    def get_task_result_prediction_for_log(self) -> str:
        """Return ``model_prediction`` text derived only from ``TaskResult`` (see ``run_coding_task``)."""
        return self._task_result_prediction or ""

    async def run_backend(
        self,
        idea: str,
        workspace: Path,
        recovery: Path | None = None,
        monitor: BaseMonitor | None = None,
        enable_lint: bool = True,
        task: Optional[Dict[str, Any]] = None,
    ) -> None:
        del enable_lint  # MagenticOne path does not use MetaGPT-style lint toggle.
        workspace = Path(workspace).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        self._task_result_prediction = ""

        task_text = _compose_task_text(idea, task)

        trace_dir = workspace / "exp_out"
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_path = trace_dir / "magentic_trace.json"
        self._cached_prompt_map = {}

        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

        await self._run_team(task_text, workspace, monitor, trace_path)
        self._last_trace_path = trace_path

        if monitor is not None:
            monitor.record_step(
                f"MagenticOne finished; structured trace: {trace_path}",
                "MagenticOne",
                RoleType.ASSISTANT,
            )

    async def _run_team(
        self,
        task_text: str,
        workspace: Path,
        monitor: BaseMonitor | None,
        trace_path: Path,
    ) -> None:
        try:
            from autogen_agentchat.base import TaskResult
        except ImportError as e:
            raise RuntimeError(
                'Install AutoGen Magentic stack, e.g. '
                'pip install -e ".[magentic]"'
            ) from e

        max_attempts = int(os.environ.get("TE_MG2_RUN_MAX_ATTEMPTS", "5"))
        retry_base = float(os.environ.get("TE_MG2_RUN_RETRY_BASE_SEC", "1"))

        model_client = _build_model_client()
        if isinstance(monitor, AttackMonitor):
            from adapter.MagenticOne.middlewares.replay import (
                apply_replay_middlewares_to_model_client,
            )

            apply_replay_middlewares_to_model_client(model_client, monitor)
        trace: dict[str, Any] = {
            "task": task_text,
            "started_at_unix": time.time(),
            "events": [],
            "attempts": [],
        }

        surfer: Any = None
        file_surfer: Any = None
        coder: Any = None
        terminal: Any = None
        flat_history: list[dict[str, Any]] = []

        include_web = not env_flag("MAGENTIC_SKIP_WEB")
        for attempt in range(max_attempts):
            self._task_result_prediction = ""
            attempt_rec: dict[str, Any] = {"index": attempt + 1, "events_before_fail": 0, "error": None}
            surfer, file_surfer, coder, terminal, team = create_magentic_team(
                model_client,
                workspace,
                include_web=include_web,
            )

            flat_history = []
            step_counter = 0
            t0 = time.perf_counter()

            try:
                async for event in team.run_stream(task=task_text):
                    elapsed = time.perf_counter() - t0
                    rec = serialize_stream_event(event)
                    rec["timestamp_s"] = elapsed
                    trace["events"].append(rec)

                    source = getattr(event, "source", None)
                    name = source if source else "unknown"
                    event_content = str(event)
                    flat_history.append(
                        {
                            "step": step_counter,
                            "event_type": type(event).__name__,
                            "content": event_content,
                            "role": "assistant" if source and source != "user" else "user",
                            "name": name,
                            "source": source,
                        }
                    )
                    step_counter += 1

                    if monitor is not None:
                        text = f"{type(event).__name__}: {event_content}"
                        # if len(text) > 12_000:
                        #     text = text[:12_000] + "…(truncated)"
                        monitor.record_step(text, str(name), RoleType.ASSISTANT)

                    if isinstance(event, TaskResult):
                        pred = extract_model_prediction_from_task_result(event)
                        if pred.strip():
                            self._task_result_prediction = pred

                attempt_rec["events_before_fail"] = step_counter
                trace["attempts"].append(attempt_rec)
                topo = build_agent_topology(flat_history)
                apply_built_topology_to_monitor(monitor, topo)
                sp = build_system_prompt(topo, flat_history, surfer, file_surfer, coder, terminal)
                self._cached_prompt_map = system_prompt_map_to_strings(sp)

                if surfer is not None:
                    await close_surfer_silently(surfer)
                break

            except Exception as exc:
                attempt_rec["events_before_fail"] = step_counter
                attempt_rec["error"] = repr(exc)
                trace["attempts"].append(attempt_rec)
                await close_surfer_silently(surfer)
                logger.warning("MagenticOne run_stream attempt %s/%s failed: %s", attempt + 1, max_attempts, exc)
                transient = is_transient_run_stream_failure(exc)
                if transient and attempt + 1 < max_attempts:
                    delay = retry_base
                    logger.info("Retrying MagenticOne in %.1fs (transient error)...", delay)
                    await asyncio.sleep(delay)
                    continue
                trace_path.write_text(
                    json.dumps(trace, ensure_ascii=False, indent=2, default=str),
                    encoding="utf-8",
                )
                closer = getattr(model_client, "aclose", None)
                if callable(closer):
                    try:
                        await closer()
                    except Exception:
                        pass
                raise

        trace_path.write_text(
            json.dumps(trace, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        closer = getattr(model_client, "aclose", None)
        if callable(closer):
            try:
                await closer()
            except Exception:
                pass

    def save_current_state(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)
        payload = {
            "adapter": "MagenticOne",
            "last_trace_path": str(self._last_trace_path) if self._last_trace_path else None,
            "model": self._model_label,
        }
        (path / "magentic_state.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_prompt_map(self) -> Dict[str, str]:
        """Merge cached prompts with defaults (``run_coding_task`` filters non-agent history names)."""
        defaults: Dict[str, str] = {
            "WebSurfer": "AutoGen MultimodalWebSurfer (see autogen_ext).",
            "FileSurfer": "AutoGen FileSurfer (see autogen_ext).",
            "Coder": "AutoGen MagenticOneCoderAgent with Windows terminal instructions.",
            "ComputerTerminal": "AutoGen CodeExecutorAgent + LocalCommandLineCodeExecutor.",
            "MagenticOneOrchestrator": "MagenticOneGroupChat orchestrator prompts (autogen_agentchat).",
        }
        return {**defaults, **self._cached_prompt_map}
