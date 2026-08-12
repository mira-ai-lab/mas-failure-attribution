"""Install global httpx hooks so every LLM HTTP request is rate-limited."""

from __future__ import annotations

import asyncio
import os
import time
from functools import wraps
from typing import Any, Callable

from utils.llm_rate_limit import (
    await_llm_slot,
    mark_llm_finish,
    min_interval_sec,
    wait_llm_slot,
)
from utils.logging import logger


def install_llm_rate_limit_hooks() -> None:
    """Patch httpx once per process; all chat/completions POSTs share one limiter."""
    if getattr(install_llm_rate_limit_hooks, "_installed", False):
        return

    patched = _patch_httpx()

    install_llm_rate_limit_hooks._installed = True
    if patched:
        logger.debug(
            "[rate-limit] hooks installed on %s "
            "(min_interval=%.2fs, anchor=finish-to-start, 429_retry=%s, 429_fallback=%.0fs)",
            ", ".join(patched),
            min_interval_sec(),
            _429_max_retries(),
            _429_retry_fallback_sec(),
        )
    else:
        logger.warning("[rate-limit] httpx unavailable; LLM rate limiting disabled")


def _is_llm_chat_completion_request(request: Any) -> bool:
    method = str(getattr(request, "method", "") or "").upper()
    if method != "POST":
        return False
    return "/chat/completions" in str(getattr(request, "url", ""))


def _httpx_caller(request: Any, label: str) -> str:
    host = getattr(getattr(request, "url", None), "host", None)
    return f"{label} host={host or 'unknown'}"


def _429_max_retries() -> int:
    raw = (os.getenv("LLM_429_MAX_RETRIES") or "1").strip()
    return int(raw) if raw.isdigit() else 1


def _429_retry_fallback_sec() -> float:
    raw = (os.getenv("LLM_429_RETRY_SEC") or "30").strip()
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 30.0


def _429_retry_delay_sec(response: Any) -> float:
    headers = getattr(response, "headers", None)
    retry_after = None
    if headers is not None:
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
    if retry_after is not None:
        text = str(retry_after).strip()
        try:
            return max(0.0, float(text))
        except ValueError:
            pass
    return _429_retry_fallback_sec()


def _log_429_response(response: Any, *, caller: str, attempt: str) -> None:
    headers = getattr(response, "headers", None)
    retry_after = None
    if headers is not None:
        retry_after = headers.get("Retry-After") or headers.get("retry-after")
    try:
        body = (response.text or "").strip()
    except Exception as exc:
        body = f"<unreadable: {exc}>"
    if len(body) > 2000:
        body = body[:2000] + "...(truncated)"
    logger.warning(
        "[rate-limit] caller=%s attempt=%s HTTP 429 status=%s retry_after=%s body=%s",
        caller,
        attempt,
        getattr(response, "status_code", "unknown"),
        retry_after,
        body or "<empty>",
    )


def _maybe_retry_on_429(
    response: Any,
    *,
    caller: str,
    resend: Callable[[], Any],
) -> Any:
    if getattr(response, "status_code", None) != 429:
        return response
    if _429_max_retries() < 1:
        _log_429_response(response, caller=caller, attempt="initial")
        return response

    _log_429_response(response, caller=caller, attempt="initial")
    delay = _429_retry_delay_sec(response)
    logger.warning(
        "[rate-limit] caller=%s retrying once after %.2fs (Retry-After or LLM_429_RETRY_SEC)",
        caller,
        delay,
    )
    time.sleep(delay)
    response = resend()
    if getattr(response, "status_code", None) == 429:
        _log_429_response(response, caller=caller, attempt="retry")
    return response


async def _maybe_retry_on_429_async(
    response: Any,
    *,
    caller: str,
    resend: Callable[[], Any],
) -> Any:
    if getattr(response, "status_code", None) != 429:
        return response
    if _429_max_retries() < 1:
        _log_429_response(response, caller=caller, attempt="initial")
        return response

    _log_429_response(response, caller=caller, attempt="initial")
    delay = _429_retry_delay_sec(response)
    logger.warning(
        "[rate-limit] caller=%s retrying once after %.2fs (Retry-After or LLM_429_RETRY_SEC)",
        caller,
        delay,
    )
    await asyncio.sleep(delay)
    response = await resend()
    if getattr(response, "status_code", None) == 429:
        _log_429_response(response, caller=caller, attempt="retry")
    return response


def _patch_httpx() -> list[str]:
    patched: list[str] = []
    try:
        import httpx
    except ImportError:
        return patched

    if not getattr(httpx.Client.send, "_llm_rate_limit_patched", False):
        original = httpx.Client.send

        @wraps(original)
        def patched_send(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            if not _is_llm_chat_completion_request(request):
                return original(self, request, *args, **kwargs)

            caller = _httpx_caller(request, "httpx.Client.send")

            def _resend() -> Any:
                return original(self, request, *args, **kwargs)

            wait_llm_slot(caller=caller)
            try:
                response = _resend()
                response = _maybe_retry_on_429(response, caller=caller, resend=_resend)
                return response
            finally:
                mark_llm_finish(caller=caller)

        patched_send._llm_rate_limit_patched = True  # type: ignore[attr-defined]
        httpx.Client.send = patched_send  # type: ignore[method-assign]
        patched.append("httpx.Client.send")

    if not getattr(httpx.AsyncClient.send, "_llm_rate_limit_patched", False):
        original_async = httpx.AsyncClient.send

        @wraps(original_async)
        async def patched_send_async(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            if not _is_llm_chat_completion_request(request):
                return await original_async(self, request, *args, **kwargs)

            caller = _httpx_caller(request, "httpx.AsyncClient.send")

            async def _resend() -> Any:
                return await original_async(self, request, *args, **kwargs)

            await await_llm_slot(caller=caller)
            try:
                response = await _resend()
                response = await _maybe_retry_on_429_async(
                    response,
                    caller=caller,
                    resend=_resend,
                )
                return response
            finally:
                mark_llm_finish(caller=caller)

        patched_send_async._llm_rate_limit_patched = True  # type: ignore[attr-defined]
        httpx.AsyncClient.send = patched_send_async  # type: ignore[method-assign]
        patched.append("httpx.AsyncClient.send")

    return patched
