"""Resolve CaptainAgent ``tool_lib`` from environment."""

from __future__ import annotations

import os
from typing import Any


def captain_tool_lib_mode() -> str:
    """Return normalized ``CAPTAIN_TOOL_LIB`` (default ``none``)."""

    raw = (os.getenv("CAPTAIN_TOOL_LIB") or "none").strip().lower()
    if raw in {"", "none", "0", "off", "false"}:
        return "none"
    if raw in {"duckduckgo", "ddg"}:
        return "duckduckgo"
    if raw == "default":
        return "default"
    raise ValueError(
        f"Unsupported CAPTAIN_TOOL_LIB={raw!r}. "
        "Use none, duckduckgo, or default."
    )


def _build_duckduckgo_tool_lib() -> list[Any]:
    try:
        from langchain_community.tools import DuckDuckGoSearchRun
        from langchain_community.utilities import DuckDuckGoSearchAPIWrapper
        from autogen.interop import Interoperability
    except ImportError as exc:
        raise RuntimeError(
            "CAPTAIN_TOOL_LIB=duckduckgo requires langchain-community and "
            "ag2 interop/duckduckgo extras. Install with:\n"
            '  pip install "ag2[interop-langchain,duckduckgo]"\n'
            "  pip install -U ddgs"
        ) from exc

    interop = Interoperability()
    try:
        api_wrapper = DuckDuckGoSearchAPIWrapper()
        langchain_tool = DuckDuckGoSearchRun(api_wrapper=api_wrapper)
    except ImportError as exc:
        raise RuntimeError(
            "CAPTAIN_TOOL_LIB=duckduckgo failed to initialize DuckDuckGo. "
            "Install the ddgs package:\n"
            "  pip install -U ddgs"
        ) from exc
    ag2_tool = interop.convert_tool(tool=langchain_tool, type="langchain")
    return [ag2_tool]


def resolve_tool_lib() -> str | list[Any] | None:
    """Return CaptainAgent ``tool_lib`` value, or ``None`` when disabled."""

    mode = captain_tool_lib_mode()
    if mode == "none":
        return None
    if mode == "duckduckgo":
        return _build_duckduckgo_tool_lib()
    return "default"
