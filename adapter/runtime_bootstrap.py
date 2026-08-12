"""Backend runtime bootstrap helpers.

This module centralizes backend-specific env/config initialization so callers
can prepare runtime requirements based on selected MAS backend.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from types import ModuleType

from utils.llm_client_hooks import install_llm_rate_limit_hooks
from utils.logging import logger


def _default_load_env_file(env_file: Path) -> None:
    """Load KEY=VALUE pairs from a dotenv-style file into os.environ."""

    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (value.startswith('"') and value.endswith('"')) or (
            value.startswith("'") and value.endswith("'")
        ):
            value = value[1:-1]
        os.environ[key] = value


def _load_backend_runtime_module(backend_name: str) -> ModuleType | None:
    """Load adapter.<backend>.runtime_config if available."""

    module_name = f"adapter.{backend_name}.runtime_config"
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError:
        return None


def bootstrap_backend_runtime(backend_name: str, env_file: Path | None) -> None:
    """Prepare backend runtime by loading env and invoking backend config hooks.

    Hook contract in adapter.<backend>.runtime_config (all optional):
    - prepare_runtime_config()
    - prepare_<backend_lower>_config()  # backward-compatible alias
    """

    runtime_module = _load_backend_runtime_module(backend_name)

    if env_file is not None:
        resolved_env = env_file.expanduser().resolve()
        os.environ["MAS_FA_ENV_FILE"] = str(resolved_env)
        _default_load_env_file(resolved_env)

    install_llm_rate_limit_hooks()

    if runtime_module is None:
        return

    if hasattr(runtime_module, "prepare_runtime_config"):
        runtime_module.prepare_runtime_config()
        return

    legacy_fn = f"prepare_{backend_name.lower()}_config"
    if hasattr(runtime_module, legacy_fn):
        getattr(runtime_module, legacy_fn)()
        return

    logger.debug(
        "Backend %s has runtime_config module but no prepare hook; skipped.",
        backend_name,
    )
