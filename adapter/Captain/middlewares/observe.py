"""Captain middleware: generate-reply-only capture, injection, and replay."""

from __future__ import annotations

import ast
from typing import Any

from adapter.Captain.prompts import captain_max_total_seeks
from adapter.Captain.seek_limit import (
    count_seek_experts_help,
    forced_captain_conclusion,
    seek_budget_exhausted,
)
from adapter.middleware import Middleware
from monitor.attack_monitor import AttackMonitor
from monitor.base_monitor import BaseMonitor, RoleType
from utils.logging import logger


def _agent_name(value: Any, fallback: str = "unknown") -> str:
    name = getattr(value, "name", None)
    if name:
        return str(name)
    if isinstance(value, str) and value:
        return value
    if value is not None:
        cls_name = value.__class__.__name__
        if cls_name:
            return cls_name
    return fallback


def _msg_text(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("content") or message)
    return str(message)


def _decode_replay_payload(payload: str) -> Any:
    """Best-effort decode for serialized assistant payloads in history."""
    if not isinstance(payload, str):
        return payload
    text = payload.strip()
    if not text:
        return payload
    if not (text.startswith("{") and text.endswith("}")):
        return payload
    try:
        decoded = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return payload
    return decoded if isinstance(decoded, dict) else payload


class ThinkMiddleware(Middleware):
    """Only hook generate-reply calls for Captain replay workflow."""

    def __init__(
        self,
        monitor: BaseMonitor | None,
        *,
        method_name: str = "generate_reply",
        record_topology: bool = True,
        topology_direction: str = "reply",
        prompt_map: dict[str, str] | None = None,
        instrument_agents: Any | None = None,
    ):
        self.monitor = monitor
        self.method_name = method_name
        self.record_topology = record_topology
        self.topology_direction = topology_direction
        self.prompt_map = prompt_map
        self.instrument_agents = instrument_agents

    @staticmethod
    def _extract_messages(ctx: Any) -> tuple[list[Any] | None, bool, int | None]:
        method_name = getattr(ctx, "_method_name", "")
        if method_name in ("_auto_select_speaker", "a_auto_select_speaker"):
            if len(ctx.args) > 2:
                return ctx.args[2], False, 2
            messages = ctx.kwargs.get("messages")
            if messages is not None:
                return messages, True, None
            return None, True, None
        messages = ctx.kwargs.get("messages")
        if messages is not None:
            return messages, True, None
        if len(ctx.args) > 0:
            return ctx.args[0], False, 0
        return None, True, None

    @staticmethod
    def _set_messages(ctx: Any, messages: list[Any], *, from_kwargs: bool, arg_index: int | None) -> None:
        if from_kwargs:
            ctx.kwargs["messages"] = messages
            return
        if arg_index is None or arg_index >= len(ctx.args):
            return
        args = list(ctx.args)
        args[arg_index] = messages
        ctx.args = tuple(args)

    def _inject_replay_prompt_system(self, ctx: Any, monitor: AttackMonitor) -> None:
        """Inject REPLAY_PROMPT into the agent's ``_oai_system_message`` (system channel).

        Aligns Captain injection with MetaGPT (``system_msgs[0]``) and MagenticOne
        (first ``SystemMessage``): the agent's persona becomes ``original_task`` inside
        REPLAY_PROMPT, and the whole REPLAY_PROMPT overwrites the system message. The
        ``messages`` channel (group-chat history with the task) is left untouched. The
        original persona is stashed on ``ctx`` and restored in ``after``.
        """
        agent = ctx.instance
        oai_sys = getattr(agent, "_oai_system_message", None)
        if not isinstance(oai_sys, list) or not oai_sys or not isinstance(oai_sys[0], dict):
            logger.warning(
                "[inject-debug] _oai_system_message shape unexpected (step=%s); "
                "cannot inject via system channel, falling back to messages path.",
                getattr(monitor, "step", "unknown"),
            )
            self._inject_replay_prompt(ctx, monitor)
            return

        orig_sys = oai_sys[0].get("content")
        if not isinstance(orig_sys, str):
            orig_sys = str(orig_sys)

        new_sys = monitor.inject_content(default_value=orig_sys)
        oai_sys[0]["content"] = new_sys
        ctx._captain_inject_orig_sys = orig_sys

        messages, _, _ = self._extract_messages(ctx)
        first = messages[0] if isinstance(messages, list) and messages else None
        first_role = first.get("role") if isinstance(first, dict) else None

        logger.info(
            "[inject-debug] system_inject applied method=%s step=%s orig_len=%s new_len=%s "
            "messages_len=%s first_msg_role=%s",
            self.method_name,
            getattr(monitor, "step", "unknown"),
            len(orig_sys or ""),
            len(new_sys or ""),
            len(messages) if isinstance(messages, list) else 0,
            first_role,
        )
        logger.info("[inject-debug] system_after=%s", (new_sys or "")[:800])

    @staticmethod
    def _resolve_oai_messages(ctx: Any) -> list[Any] | None:
        """Fall back to agent._oai_messages[sender] like AG2 generate_oai_reply does.

        AG2 GroupChatManager calls generate_reply(sender=self) WITHOUT a messages
        argument, relying on generate_oai_reply to use self._oai_messages[sender].
        Our middleware must perform the same fallback before injecting, otherwise
        the group chat history is lost and the expert sees only the REPLAY message.
        """

        agent = ctx.instance
        sender = ctx.kwargs.get("sender")
        if sender is None and len(ctx.args) > 1:
            sender = ctx.args[1]
        if sender is None:
            return None
        oai_messages = getattr(agent, "_oai_messages", None)
        if not isinstance(oai_messages, dict) or sender not in oai_messages:
            return None
        history = oai_messages[sender]
        return list(history) if isinstance(history, list) else None

    def _inject_replay_prompt(self, ctx: Any, monitor: AttackMonitor) -> None:
        messages, from_kwargs, arg_index = self._extract_messages(ctx)

        # AG2 group chat calls generate_reply(sender=self) without messages;
        # fall back to _oai_messages[sender] to recover the group chat history
        # before injecting, instead of treating None as an empty list.
        fell_back = False
        if messages is None:
            fallback = self._resolve_oai_messages(ctx)
            if fallback:
                messages = fallback
                fell_back = True

        patched: list[Any] = list(messages) if isinstance(messages, list) else []
        should_inject = monitor.should_inject()

        logger.info(
            "[inject-debug] method=%s step=%s should_inject=%s messages_len=%s fell_back=%s",
            self.method_name,
            getattr(monitor, "step", "unknown"),
            should_inject,
            len(patched),
            fell_back,
        )

        if not patched:
            injected = monitor.inject_content(default_value="")
            if should_inject:
                logger.info("[inject-debug] injected_content=%s", injected)
            patched.insert(0, {"role": "system", "content": injected})
            self._set_messages(ctx, patched, from_kwargs=from_kwargs, arg_index=arg_index)
            if should_inject:
                read_back, _, _ = self._extract_messages(ctx)
                first = read_back[0] if isinstance(read_back, list) and read_back else None
                logger.info("[inject-debug] injected_readback_first=%s", first)
            return

        if fell_back:
            # Group chat history recovered from _oai_messages: prepend REPLAY
            # as a new system message, preserving all existing history.
            injected = monitor.inject_content(default_value="")
            if should_inject:
                logger.info("[inject-debug] injected_content=%s", injected)
            patched.insert(0, {"role": "system", "content": injected})
            self._set_messages(ctx, patched, from_kwargs=from_kwargs, arg_index=arg_index)
            if should_inject:
                read_back, _, _ = self._extract_messages(ctx)
                first = read_back[0] if isinstance(read_back, list) and read_back else None
                logger.info("[inject-debug] injected_readback_first=%s", first)
            return

        first = patched[0]
        if isinstance(first, dict):
            original = first.get("content")
            if not isinstance(original, str):
                original = str(original)
            injected = monitor.inject_content(default_value=original)
            if should_inject:
                logger.info("[inject-debug] injected_content=%s", injected)
            new_first = dict(first)
            new_first["content"] = injected
            patched[0] = new_first
            self._set_messages(ctx, patched, from_kwargs=from_kwargs, arg_index=arg_index)
            if should_inject:
                read_back, _, _ = self._extract_messages(ctx)
                first_rb = read_back[0] if isinstance(read_back, list) and read_back else None
                logger.info("[inject-debug] injected_readback_first=%s", first_rb)
            return

        original = str(first)
        injected = monitor.inject_content(default_value=original)
        if should_inject:
            logger.info("[inject-debug] injected_content=%s", injected)
        patched[0] = injected
        self._set_messages(ctx, patched, from_kwargs=from_kwargs, arg_index=arg_index)
        if should_inject:
            read_back, _, _ = self._extract_messages(ctx)
            first_rb = read_back[0] if isinstance(read_back, list) and read_back else None
            logger.info("[inject-debug] injected_readback_first=%s", first_rb)

    def before(self, ctx):
        ctx._method_name = self.method_name

        is_select = self.method_name in ("_auto_select_speaker", "a_auto_select_speaker")
        is_generate = self.method_name in ("generate_reply", "a_generate_reply")
        
        #只处理两类方法：is_select：选发言人，is_generate：生成回复
        #如果都不是，直接返回 None → 放行，不做任何拦截。
        if not (is_select or is_generate):
            return None

        monitor = self.monitor
        if monitor is None:
            return None

        src_name = ""
        sender_name = ""
        can_replay = True
        if is_generate:
            src_name = _agent_name(ctx.instance)
            sender = ctx.kwargs.get("sender")
            if sender is None and len(ctx.args) > 1:
                sender = ctx.args[1]
            sender_name = _agent_name(sender, fallback="")
            ctx._captain_reply = (src_name, sender_name)
            #判断这条调用是否允许 replay。只有来自 chat_manager 的消息，
            #或者 CaptainAgent 收到 Expert_summoner 的消息才允许 replay。
            #其他内部 agent（如 speaker_selection_agent）的 generate_reply 不允许 replay，避免重复。什么意思
            can_replay = (
                sender_name == "chat_manager"
                or (src_name == "CaptainAgent" and sender_name == "Expert_summoner")
            )
        if isinstance(monitor, AttackMonitor):
            # Attack step: inject in before-hook, then let real call run.
            if monitor.should_inject():
                if is_generate:
                    # Inject via the agent's system channel (aligns with MetaGPT/MagenticOne):
                    # overwrite _oai_system_message with REPLAY_PROMPT(persona + injection),
                    # leave messages (group-chat history with task) untouched. Restored in after().
                    self._inject_replay_prompt_system(ctx, monitor)
                elif is_select:
                    # Speaker selection has no persona to override; inject via messages.
                    self._inject_replay_prompt(ctx, monitor)
                return None

            # Before injection: strict replay. After injection: live run.
            if not monitor.is_injected():
                if is_generate and not can_replay:
                    return None

                try:
                    replayed = monitor.get_current_reply()
                except Exception:
                    replayed = None
                if replayed is not None:
                    if is_select:
                        selected_agent = ctx.instance.agent_by_name(str(replayed))
                        if selected_agent is None:
                            return None
                        logger.info(
                            "[inject-debug] replay select_speaker step=%s selected=%s",
                            getattr(monitor, "step", "unknown"),
                            getattr(selected_agent, "name", replayed),
                        )
                        return selected_agent

                    logger.info(
                        "[inject-debug] replay generate_reply step=%s src=%s sender=%s",
                        getattr(monitor, "step", "unknown"),
                        src_name,
                        sender_name,
                    )
                    return _decode_replay_payload(replayed)

        if is_generate and src_name == "CaptainAgent":
            history = getattr(monitor, "history", None) or []
            if seek_budget_exhausted(history):
                used = count_seek_experts_help(history)
                logger.warning(
                    "[captain-seek-limit] budget exhausted (%s/%s seek_experts_help); forcing conclusion",
                    used,
                    captain_max_total_seeks(),
                )
                return forced_captain_conclusion(history)

        return None

    def after(self, ctx, result):
        # Restore _oai_system_message if we overwrote it for injection (generate path).
        # Must run before any other branch so the persona is restored even on early returns.
        orig_sys = getattr(ctx, "_captain_inject_orig_sys", None)
        if orig_sys is not None:
            try:
                oai_sys = getattr(ctx.instance, "_oai_system_message", None)
                if isinstance(oai_sys, list) and oai_sys and isinstance(oai_sys[0], dict):
                    oai_sys[0]["content"] = orig_sys
                    logger.info(
                        "[inject-debug] system restored after inject step=%s",
                        getattr(self.monitor, "step", "unknown"),
                    )
                else:
                    logger.warning(
                        "[inject-debug] cannot restore _oai_system_message (shape changed); "
                        "persona may stay overwritten for step=%s",
                        getattr(self.monitor, "step", "unknown"),
                    )
            except Exception as exc:
                logger.warning(
                    "[inject-debug] failed to restore _oai_system_message: %s", exc
                )
            ctx._captain_inject_orig_sys = None

        if self.method_name == "_create_internal_agents":
            if (
                self.instrument_agents is not None
                and isinstance(result, tuple)
                and len(result) >= 2
            ):
                speaker_selection_agent = result[1]
                if speaker_selection_agent is not None:
                    self.instrument_agents([speaker_selection_agent], self.monitor)
            return result

        if self.method_name in ("_auto_select_speaker", "a_auto_select_speaker"):
            if self.monitor is not None:
                selected_name = getattr(result, "name", None)
                if selected_name:
                    self.monitor.record_step(str(selected_name), "speaker_selection_agent", RoleType.ASSISTANT)
                    self.monitor.record_topology("speaker_selection_agent", str(selected_name))
            return result

        if self.monitor is None or self.method_name not in ("generate_reply", "a_generate_reply"):
            return result

        payload = getattr(ctx, "_captain_reply", None)
        if payload is None:
            return result
        src_name, sender_name = payload

        # Selection decisions are recorded in _auto_select_speaker middleware.
        # Skip the internal selector agent's generate_reply to avoid duplicate steps.
        if src_name == "speaker_selection_agent":
            return result

        if self.record_topology and sender_name and src_name:
            if self.topology_direction == "forward":
                self.monitor.record_topology(src_name, sender_name)
            else:
                self.monitor.record_topology(sender_name, src_name)

        content = _msg_text(result) if result is not None else ""
        if content:
            self.monitor.record_step(content, src_name, RoleType.ASSISTANT)
        return result
