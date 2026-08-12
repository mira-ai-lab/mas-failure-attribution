"""Global LLM request rate limiter shared across pipeline backends."""

from __future__ import annotations

import asyncio
import os
import threading
import time

from utils.logging import logger

_lock = threading.Lock()
_last_finish_at: float | None = None


def min_interval_sec() -> float:
    """Minimum seconds between LLM request finish and the next request start."""
    raw = (
        os.getenv("LLM_MIN_INTERVAL_SEC")
        or os.getenv("CAPTAIN_LLM_CALL_DELAY_SEC")
        or "2.5"
    ).strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 2.5


def wait_llm_slot(*, caller: str = "unknown") -> float:
    """Block until ``min_interval_sec`` has elapsed since the last LLM HTTP finish."""
    interval = min_interval_sec()
    if interval <= 0:
        logger.debug("[rate-limit] caller=%s disabled (interval=0)", caller)
        return 0.0

    with _lock:
        now = time.monotonic()
        elapsed = (now - _last_finish_at) if _last_finish_at is not None else float("inf")
        wait = interval - elapsed
        if wait > 0:
            logger.debug(
                "[rate-limit] caller=%s sleeping %.2fs since last finish "
                "(min_interval=%.2fs, elapsed=%.2fs)",
                caller,
                wait,
                interval,
                elapsed if _last_finish_at is not None else 0.0,
            )
            time.sleep(wait)
            slept = wait
        else:
            logger.debug(
                "[rate-limit] caller=%s proceed since last finish "
                "(min_interval=%.2fs, elapsed=%.2fs)",
                caller,
                interval,
                elapsed if _last_finish_at is not None else 0.0,
            )
            slept = 0.0
        logger.debug("[rate-limit] caller=%s sending request", caller)
        return slept


def mark_llm_finish(*, caller: str = "unknown") -> None:
    """Record LLM HTTP completion; the next call waits from this timestamp."""
    global _last_finish_at

    with _lock:
        _last_finish_at = time.monotonic()
        logger.debug("[rate-limit] caller=%s request finished", caller)


async def await_llm_slot(*, caller: str = "unknown") -> float:
    """Async wrapper that runs the global limiter without blocking the event loop."""
    return await asyncio.to_thread(wait_llm_slot, caller=caller)
