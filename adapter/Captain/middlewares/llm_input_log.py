"""Log the exact LLM payload (_oai_system_message + messages) before API calls."""

from __future__ import annotations

import os
from typing import Any

from adapter.middleware import Middleware
from monitor.attack_monitor import AttackMonitor
from monitor.base_monitor import BaseMonitor
from utils.logging import logger


def captain_log_llm_input_mode() -> str:
    """Return logging mode: off | inject | all."""
    raw = (os.getenv("CAPTAIN_LOG_LLM_INPUT") or "").strip().lower()
    if raw in ("", "0", "false", "no", "off"):
        return "off"
    if raw in ("all", "full", "2"):
        return "all"
    return "inject"


def _preview_chars() -> int:
    try:
        return max(0, int(os.getenv("CAPTAIN_LOG_LLM_INPUT_PREVIEW", "200")))
    except ValueError:
        return 200


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


def _msg_content(message: Any) -> str:
    if isinstance(message, dict):
        content = message.get("content")
        if content is None:
            tool_calls = message.get("tool_calls")
            if tool_calls:
                return str(tool_calls)
            return str(message)
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text") or item))
                else:
                    parts.append(str(item))
            return "\n".join(parts)
        return str(content)
    return str(message)


def _resolve_processed_messages(agent: Any, ctx: Any) -> tuple[list[Any], Any]:
    """Mirror generate_oai_reply message resolution before the API call."""
    messages = ctx.kwargs.get("messages")
    if messages is None and len(ctx.args) > 0:
        messages = ctx.args[0]

    sender = ctx.kwargs.get("sender")
    if sender is None and len(ctx.args) > 1:
        sender = ctx.args[1]

    if messages is None and sender is not None:
        oai_messages = getattr(agent, "_oai_messages", None)
        if isinstance(oai_messages, dict) and sender in oai_messages:
            messages = oai_messages[sender]

    if not isinstance(messages, list):
        messages = []

    system = getattr(agent, "_oai_system_message", None) or []
    if not isinstance(system, list):
        system = [system]
    return list(system) + list(messages), sender


def _should_log(monitor: BaseMonitor | None, mode: str) -> tuple[bool, str]:
    if mode == "off" or monitor is None:
        return False, ""
    if mode == "all":
        return True, "llm-input"
    if isinstance(monitor, AttackMonitor):
        if monitor.is_injected() and monitor.step == monitor.attack_step:
            return True, "llm-input-inject"
    return False, ""


def log_llm_input(
    processed_messages: list[Any],
    *,
    tag: str,
    agent_name: str,
    sender_name: str,
    step: Any = "unknown",
) -> None:
    preview_len = _preview_chars()
    logger.info(
        "[%s] step=%s agent=%s sender=%s message_count=%s",
        tag,
        step,
        agent_name,
        sender_name,
        len(processed_messages),
    )
    for index, message in enumerate(processed_messages):
        role = message.get("role", "?") if isinstance(message, dict) else "?"
        text = _msg_content(message)
        total_len = len(text)
        preview = text[:preview_len]
        if total_len > preview_len:
            preview = f"{preview}…(+{total_len - preview_len} chars)"
        logger.info(
            "[%s] msg[%s] role=%s len=%s preview=%r",
            tag,
            index,
            role,
            total_len,
            preview,
        )


class LlmInputLogMiddleware(Middleware):
    """Hook generate_oai_reply to log _oai_system_message + messages."""

    def __init__(self, monitor: BaseMonitor | None):
        self.monitor = monitor

    def before(self, ctx):
        mode = captain_log_llm_input_mode()
        should_log, tag = _should_log(self.monitor, mode)
        if not should_log:
            return None

        agent = ctx.instance
        processed_messages, sender = _resolve_processed_messages(agent, ctx)
        step = getattr(self.monitor, "step", "unknown")
        log_llm_input(
            processed_messages,
            tag=tag,
            agent_name=_agent_name(agent),
            sender_name=_agent_name(sender, fallback=""),
            step=step,
        )
        return None
