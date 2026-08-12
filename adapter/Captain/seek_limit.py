"""Enforce Captain ``seek_experts_help`` call budget at runtime."""

from __future__ import annotations

import re

from adapter.Captain.prompts import captain_max_total_seeks


def count_seek_experts_help(history: list) -> int:
    """Count prior CaptainAgent ``seek_experts_help`` tool calls in monitor history."""
    total = 0
    for entry in history:
        name = getattr(entry, "name", None)
        content = getattr(entry, "content", "") or ""
        if name != "CaptainAgent":
            continue
        if "seek_experts_help" in content and "tool_calls" in content:
            total += 1
    return total


def seek_budget_exhausted(history: list) -> bool:
    """Return True when another ``seek_experts_help`` call would exceed the budget."""
    return count_seek_experts_help(history) >= captain_max_total_seeks()


def forced_captain_conclusion(history: list) -> str:
    """Synthesize a final CaptainAgent reply when re-seek budget is exhausted."""
    latest_summary = ""
    for entry in reversed(history):
        if getattr(entry, "name", None) == "Expert_summoner":
            latest_summary = str(getattr(entry, "content", "") or "")
            if latest_summary.strip():
                break

    boxed = _extract_boxed_answer(latest_summary)
    if boxed is not None:
        return (
            "The seek-experts verification budget has been reached. "
            f"I am concluding with the latest expert result: \\boxed{{{boxed}}}.\n\nTERMINATE"
        )

    if latest_summary.strip():
        return (
            "The seek-experts verification budget has been reached. "
            "I am concluding with the latest expert summary below.\n\n"
            f"{latest_summary.strip()}\n\nTERMINATE"
        )

    return (
        "The seek-experts verification budget has been reached. "
        "Please refer to the prior expert conversation for the final result.\n\nTERMINATE"
    )


def _extract_boxed_answer(text: str) -> str | None:
    for pattern in (
        r"\\boxed\{([^}]*)\}",
        r"\boxed\{([^}]*)\}",
        r"\\\(\\boxed\{([^}]*)\}\\\)",
    ):
        match = re.search(pattern, text)
        if match:
            value = match.group(1).strip()
            if value:
                return value
    return None
