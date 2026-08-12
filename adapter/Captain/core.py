"""Captain backend adapter (AG2/AutoGen).
Trace capture uses runtime hooks so monitor history remains replay-compatible.
"""

from __future__ import annotations

import json
import os
import types
from pathlib import Path
from typing import Any

from adapter.base_adapter import BaseAdapter
from adapter.middleware import build_llm_wrapper
from adapter.middleware import patch_with_middlewares
from adapter.Captain.middlewares import LlmInputLogMiddleware, ThinkMiddleware
from adapter.Captain.prompts import captain_max_reseeks, get_captain_system_message
from adapter.Captain.tool_lib import captain_tool_lib_mode, resolve_tool_lib
from monitor.attack_monitor import AttackMonitor
from monitor.base_monitor import BaseMonitor, RoleType
from utils.logging import logger


class CaptainAdapter(BaseAdapter):
    """Minimal Captain adapter compatible with the pipeline backend contract."""

    def __init__(self) -> None:
       
        os.environ.setdefault("AUTOGEN_USE_DOCKER", "0")
        self._prompt_map: dict[str, str] = {}
        self._dynamic_prompt_map: dict[str, str] = {}
        self._max_turns = int(os.getenv("CAPTAIN_MAX_TURNS", "1"))  # Outer Captain conversation round cap.
        self._temperature = float(os.getenv("CAPTAIN_TEMPERATURE", "0"))  # Shared LLM sampling temperature.
        self._group_max_round = int(os.getenv("CAPTAIN_GROUP_MAX_ROUND", "6")) # Inner AutoBuild group-chat round cap.
        # AG2 nested chat default is 5; 1 leaves tool_calls without role=tool and breaks reflection_with_llm.
        self._nested_max_turns = int(os.getenv("CAPTAIN_NESTED_MAX_TURNS", "3"))
        self._max_reseeks = captain_max_reseeks()
        self._freeze_group_identity = os.getenv("CAPTAIN_FREEZE_GROUP_IDENTITY", "1").strip() != "0"
        self.last_agent_library_info: dict[str, Any] = {}
        self._code_work_dir: str | None = None

    def _build_llm_config(self) -> Any:
        """Build AG2-compatible LLM config from environment variables only."""

        model = os.getenv("CAPTAIN_MODEL", os.getenv("OPENAI_MODEL", "gpt-5")).strip()
        api_key = os.getenv("CAPTAIN_API_KEY", os.getenv("OPENAI_API_KEY", "")).strip()
        if not api_key:
            raise RuntimeError("Set CAPTAIN_API_KEY or OPENAI_API_KEY before running Captain backend.")
        base_url = os.getenv("CAPTAIN_BASE_URL", os.getenv("OPENAI_BASE_URL", "")).strip()
        cfg: dict[str, Any] = {"model": model, "api_key": api_key}
        if base_url:
            cfg["base_url"] = base_url
        max_retries_raw = os.getenv("CAPTAIN_MAX_RETRIES", "0").strip()
        if max_retries_raw.isdigit():
            cfg["max_retries"] = int(max_retries_raw)
        return {
            "config_list": [cfg],
            "temperature": self._temperature,
            "timeout": 300,
        }

    async def run_backend(
        self,
        idea: str,
        workspace: Path,
        recovery: Path = None,
        monitor: BaseMonitor = None,
        task_id: str | None = None,
        ):
        """Run one Captain task with middleware-based replay-compatible tracing."""

        from autogen import UserProxyAgent
        from autogen.agentchat.contrib.captainagent import CaptainAgent

        llm_config = self._build_llm_config()
        self._dynamic_prompt_map = {}
        agent_library_path, library_info = self._resolve_agent_library_path(workspace, task_id)
        self.last_agent_library_info = library_info
        agent_lib = self._resolve_agent_lib_json(agent_library_path)

        workspace.mkdir(parents=True, exist_ok=True)
        self._code_work_dir = str(workspace.resolve())
        code_execution_config = self._build_code_execution_config()
        tool_lib = resolve_tool_lib()
        tool_lib_mode = captain_tool_lib_mode()
        if tool_lib is None:
            logger.info("[captain-tool-lib] mode=%s (nested experts have no tool_lib)", tool_lib_mode)
        elif isinstance(tool_lib, str):
            logger.info("[captain-tool-lib] mode=%s tool_lib=%s", tool_lib_mode, tool_lib)
        else:
            tool_names = [
                getattr(tool, "name", None) or getattr(tool, "__name__", type(tool).__name__)
                for tool in tool_lib
            ]
            logger.info(
                "[captain-tool-lib] mode=%s tools=%s",
                tool_lib_mode,
                tool_names,
            )

        captain_kwargs: dict[str, Any] = {
            "name": "Captain",
            "system_message": get_captain_system_message(max_reseeks=self._max_reseeks),
            "llm_config": llm_config,
            "code_execution_config": code_execution_config.copy(),
            "nested_config": {
                "group_chat_config": {"max_round": self._group_max_round},
                "max_turns": self._nested_max_turns,
                "autobuild_build_config": {
                    "code_execution_config": code_execution_config.copy(),
                },
            },
            "agent_config_save_path": agent_library_path,
        }
        if tool_lib is not None:
            captain_kwargs["tool_lib"] = tool_lib

        captain = CaptainAgent(**captain_kwargs)

        saved_build_history = self._load_saved_build_history(workspace, agent_library_path)
        if saved_build_history and getattr(captain, "executor", None) is not None:
            captain.executor.build_history.update(saved_build_history)

        user_proxy = UserProxyAgent(
            name="CaptainUserProxy",
            human_input_mode="NEVER",
        )

        participants = self._collect_participants(captain, user_proxy)
        self._instrument_agents(participants, monitor)
        self._install_dynamic_agent_hooks(captain, monitor)

        result = user_proxy.initiate_chat(captain, message=idea, max_turns=self._max_turns)

        self._prompt_map = self._collect_prompt_map(captain, user_proxy)
        return result.summary if hasattr(result, "summary") else str(result)

    @staticmethod
    def _resolve_agent_library_path(workspace: Path, task_id: str | None = None) -> tuple[Path, dict[str, Any]]:
        """Return the round_0 task workspace used as the Captain agent library."""

        workspace = workspace.resolve()
        round_dir = workspace.parent
        data_source_dir = round_dir.parent

        task_dir_name = (task_id or "").strip()
        if not task_dir_name:
            task_dir_name = workspace.name
            for suffix in ("_attack_analysis", "_diagnose_analysis"):
                if task_dir_name.endswith(suffix):
                    task_dir_name = task_dir_name[: -len(suffix)]
                    break

        library_path = data_source_dir / "round_0" / task_dir_name
        library_path.mkdir(parents=True, exist_ok=True)

        has_history = any(library_path.glob("build_history_*.json"))
        mode = "reuse" if has_history else "create"
        from utils.logging import logger

        info = {
            "task_id": task_dir_name,
            "mode": mode,
            "path": str(library_path),
        }
        logger.info(
            "[captain-lib] task=%s mode=%s path=%s",
            task_dir_name,
            mode,
            library_path,
        )
        return library_path, info

    @staticmethod
    def _load_saved_build_history(workspace: Path, agent_library_path: Path) -> dict[str, Any]:
        """Load saved Captain build_history so replay rounds can reuse exact agent configs."""

        if workspace.resolve().parent.name == "round_0":
            return {}

        build_history_files = sorted(agent_library_path.glob("build_history_*.json"))
        if not build_history_files:
            return {}

        merged_history: dict[str, Any] = {}

        for history_file in build_history_files:
            try:
                payload = json.loads(history_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            merged_history.update(payload)

        from utils.logging import logger

        logger.info(
            "[captain-lib] loaded saved build_history groups=%s from=%s",
            sorted(merged_history.keys()),
            agent_library_path,
        )
        return merged_history

    @staticmethod
    def _resolve_agent_lib_json(agent_library_path: Path) -> str | None:
        """Return latest build_history JSON file path for CaptainAgent agent_lib, or None."""

        build_history_files = sorted(agent_library_path.glob("build_history_*.json"))
        if not build_history_files:
            return None
        return str(build_history_files[-1])

       
    def get_prompt_map(self) -> dict[str, str]:
        """Return collected role->system prompt mapping for logging."""
        if self._prompt_map:
            return self._prompt_map
        return {"Captain": "CaptainAgent coordinator"}

    def _build_code_execution_config(self) -> dict[str, Any]:
        """Return AG2 code-execution config bound to the current task workspace."""

        work_dir = (self._code_work_dir or ".").strip()
        return {
            "use_docker": False,
            "work_dir": work_dir,
            "last_n_messages": 1,
            "timeout": 300,
        }

    def _sync_nested_code_execution_config(self, executor: Any) -> dict[str, Any]:
        """Push the active workspace into nested AutoBuild and executor configs."""

        code_execution_config = self._build_code_execution_config()
        nested_config = getattr(executor, "_nested_config", None)
        if isinstance(nested_config, dict):
            autobuild_build_config = nested_config.setdefault("autobuild_build_config", {})
            if isinstance(autobuild_build_config, dict):
                autobuild_build_config["code_execution_config"] = code_execution_config.copy()

        executor_code_config = getattr(executor, "_code_execution_config", None)
        if isinstance(executor_code_config, dict):
            executor_code_config.update(code_execution_config)

        from utils.logging import logger

        logger.info("[captain-workdir] nested Computer_terminal work_dir=%s", code_execution_config["work_dir"])
        return code_execution_config

    def _install_dynamic_agent_hooks(self, captain: Any, monitor: BaseMonitor | None) -> None:
        """Patch dynamically created agents inside Captain AutoBuild."""

        executor = getattr(captain, "executor", None)
        if executor is None or not hasattr(executor, "_run_autobuild"):
            return
        if getattr(executor, "_captain_dynamic_hook_installed", False):
            return

        original_run = executor._run_autobuild

        def wrapped_run(exec_self, *args, **kwargs):
            # Keep the nested expert group identity stable across replay rounds.
            # This avoids rebuilding a different agent set for the same task.
            self._force_replay_group_name(
                exec_self,
                monitor,
                args,
                kwargs,
                freeze_group_identity=self._freeze_group_identity,
            )

            code_execution_config = self._sync_nested_code_execution_config(exec_self)

            globals_dict = getattr(original_run, "__globals__", {})
            builder_cls = globals_dict.get("AgentBuilder")
            groupchat_cls = globals_dict.get("GroupChat")

            originals: dict[tuple[Any, str], Any] = {}
            if builder_cls is not None:
                originals.update(self._patch_builder_methods(builder_cls, monitor, code_execution_config))
            if groupchat_cls is not None:
                originals.update(self._patch_groupchat_methods(groupchat_cls, monitor))

            try:
                return original_run(*args, **kwargs)
            finally:
                for (owner, method_name), method in originals.items():
                    setattr(owner, method_name, method)

        executor._run_autobuild = types.MethodType(wrapped_run, executor)
        executor._captain_dynamic_hook_installed = True

    @staticmethod
    def _force_replay_group_name(
        executor: Any,
        monitor: BaseMonitor | None,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
        freeze_group_identity: bool,
    ) -> None:
        """Force _run_autobuild to reuse a saved group config during replay."""

        if not freeze_group_identity:
            return

        build_history = getattr(executor, "build_history", None)
        if not isinstance(build_history, dict) or not build_history:
            return

        requested_group_name = kwargs.get("group_name")
        if requested_group_name is None and len(args) > 0:
            requested_group_name = args[0]

        if isinstance(requested_group_name, str) and requested_group_name in build_history:
            return

        # Use a stable key order so reruns map to the same expert group identity.
        fixed_group_name = sorted(build_history.keys())[0]
        kwargs["group_name"] = fixed_group_name

    def _patch_builder_methods(
        self,
        builder_cls: Any,
        monitor: BaseMonitor | None,
        code_execution_config: dict[str, Any] | None = None,
    ) -> dict[tuple[Any, str], Any]:
        """Temporarily patch builder methods to instrument returned agents.

        For ``load`` (the R1 replay path), also force the current workspace into
        ``code_execution_config`` so build_history replay does not fall back to
        the stale ``groupchat`` directory. This merges the former
        ``_patch_agent_builder_load`` into the same wrapper to avoid two wrappers
        racing for ``AgentBuilder.load``: the previous double-patch left the expert
        ``generate_reply`` hook uninstalled on R1, which made ``spoke_experts`` empty
        and misrouted the inject step onto ``_auto_select_speaker``.
        """

        originals: dict[tuple[Any, str], Any] = {}
        for method_name in ("build", "build_from_library", "load"):
            method = getattr(builder_cls, method_name, None)
            if method is None:
                continue

            originals[(builder_cls, method_name)] = method

            is_load = method_name == "load"
            cfg = code_execution_config if is_load else None

            def wrapped_method(*m_args, _orig=method, _cfg=cfg, **m_kwargs):
                if _cfg is not None:
                    m_kwargs["code_execution_config"] = _cfg.copy()
                result = _orig(*m_args, **m_kwargs)
                self._instrument_result_agents(result, monitor)
                return result

            setattr(builder_cls, method_name, wrapped_method)

        return originals

    def _patch_groupchat_methods(self, groupchat_cls: Any, monitor: BaseMonitor | None) -> dict[tuple[Any, str], Any]:
        """Temporarily patch GroupChat speaker-selection internals."""

        originals: dict[tuple[Any, str], Any] = {}
        for method_name in ("_create_internal_agents", "_auto_select_speaker", "a_auto_select_speaker"):
            method = getattr(groupchat_cls, method_name, None)
            if method is None:
                continue

            originals[(groupchat_cls, method_name)] = method
            middlewares = [
                ThinkMiddleware(
                    monitor,
                    method_name=method_name,
                    record_topology=True,
                    prompt_map=self._dynamic_prompt_map,
                    instrument_agents=self._instrument_agents,
                )
            ]
            setattr(groupchat_cls, method_name, build_llm_wrapper(method, middlewares))

        return originals

    def _instrument_result_agents(self, result: Any, monitor: BaseMonitor | None) -> None:
        """Instrument agent lists returned by Captain AutoBuild."""

        if not isinstance(result, tuple) or not result:
            return

        agent_list = result[0]
        if not isinstance(agent_list, list):
            return

        self._instrument_agents(agent_list, monitor)

    def _instrument_agents(self, agents: list[Any], monitor: BaseMonitor | None) -> None:
        """Apply minimal runtime hooks: generate-reply only."""

        for agent in agents:
            self._safe_patch(
                agent,
                "generate_reply",
                [
                    ThinkMiddleware(
                        monitor,
                        method_name="generate_reply",
                        record_topology=True,
                        prompt_map=self._dynamic_prompt_map,
                    )
                ],
            )
            self._safe_patch(
                agent,
                "a_generate_reply",
                [
                    ThinkMiddleware(
                        monitor,
                        method_name="a_generate_reply",
                        record_topology=True,
                        prompt_map=self._dynamic_prompt_map,
                    )
                ],
            )
            llm_input_log = [LlmInputLogMiddleware(monitor)]
            self._safe_patch(agent, "generate_oai_reply", llm_input_log)
            self._wire_patched_oai_reply(agent)

            name = getattr(agent, "name", None)
            system_message = getattr(agent, "system_message", None)
            if system_message is None:
                system_message = getattr(agent, "description", None)
            if name and system_message is not None:
                self._dynamic_prompt_map[str(name)] = str(system_message)

    @staticmethod
    def _collect_participants(captain: Any, user_proxy: Any) -> list[Any]:
        """Collect known Captain participants for replay control and prompt capture."""

        participants: list[Any] = [captain, user_proxy]
        for attr in ("assistant", "executor"):
            agent = getattr(captain, attr, None)
            if agent is not None and agent not in participants:
                participants.append(agent)
        return participants

    @staticmethod
    def _safe_patch(instance: Any, method_name: str, middlewares: list[Any]) -> None:
        """Patch a method if it exists; ignore unsupported hook points."""

        if not hasattr(instance, method_name):
            return

        patched = getattr(instance, "_captain_patched_methods", None)
        if not isinstance(patched, set):
            patched = set()
            setattr(instance, "_captain_patched_methods", patched)
        if method_name in patched:
            return

        patch_with_middlewares(instance, method_name, middlewares)
        patched.add(method_name)

    @staticmethod
    def _wire_patched_oai_reply(agent: Any) -> None:
        """Point AG2 _reply_func_list at patched generate_oai_reply.

        generate_reply() calls reply functions from _reply_func_list using the
        class attribute ConversableAgent.generate_oai_reply, which bypasses
        instance-level patches unless we replace the registered function.

        AG2 invokes reply funcs as ``fn(recipient, messages=..., sender=..., config=...)``.
        Our patched method is already bound to ``recipient``, so we must adapt the call
        signature instead of registering the bound method directly.
        """

        try:
            from autogen.agentchat.conversable_agent import ConversableAgent
        except ImportError:
            return

        replace = getattr(agent, "replace_reply_func", None)
        if not callable(replace):
            return

        patched_sync = getattr(agent, "generate_oai_reply", None)
        if patched_sync is not None:

            def hooked_sync(
                recipient: Any,
                messages: Any = None,
                sender: Any = None,
                config: Any = None,
                **kwargs: Any,
            ):
                return patched_sync(messages=messages, sender=sender, config=config, **kwargs)

            hooked_sync.__name__ = "generate_oai_reply"
            replace(ConversableAgent.generate_oai_reply, hooked_sync)

        patched_async = getattr(agent, "a_generate_oai_reply", None)
        if patched_async is not None:

            async def hooked_async(
                recipient: Any,
                messages: Any = None,
                sender: Any = None,
                config: Any = None,
                **kwargs: Any,
            ):
                return await patched_async(
                    messages=messages,
                    sender=sender,
                    config=config,
                    **kwargs,
                )

            hooked_async.__name__ = "a_generate_oai_reply"
            replace(ConversableAgent.a_generate_oai_reply, hooked_async)

    def _collect_prompt_map(self, captain: Any, user_proxy: Any | None = None) -> dict[str, str]:
        """Collect prompt map for Captain and dynamically created experts.

        Prompt sources are queried in order:
        1. ``captain.system_message``
        2. ``captain._agents`` (if present)
        3. ``captain.chat_messages`` peers (captures late-created experts)
        """

        prompt_map: dict[str, str] = {}

        def _add_prompt(agent: Any) -> None:
            if agent is None:
                return
            name = getattr(agent, "name", None)
            if not name:
                return
            system_message = getattr(agent, "system_message", None)
            if system_message is None:
                system_message = getattr(agent, "description", None)
            if system_message is not None:
                prompt_map[str(name)] = str(system_message)

        _add_prompt(captain)
        _add_prompt(getattr(captain, "assistant", None))
        _add_prompt(getattr(captain, "executor", None))
        _add_prompt(user_proxy)

        if getattr(captain, "_agents", None):
            for agent_name, agent in captain._agents.items():
                sys_prompt = getattr(agent, "system_message", None)
                if sys_prompt is not None:
                    prompt_map[str(agent_name)] = str(sys_prompt)

        if getattr(captain, "chat_messages", None):
            for peer in captain.chat_messages.keys():
                peer_name = getattr(peer, "name", None)
                peer_prompt = getattr(peer, "system_message", None)
                if peer_name and peer_prompt is not None and peer_name not in prompt_map:
                    prompt_map[str(peer_name)] = str(peer_prompt)

        for name, prompt in self._dynamic_prompt_map.items():
            prompt_map[name] = prompt

        return prompt_map
