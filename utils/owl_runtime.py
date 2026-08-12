"""Helpers for OWL runtime dependency checks and environment loading."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


OWL_ENV_FILE_ENV = "OWL_ENV_FILE"
MAS_ENV_FILE_ENV = "MAS_FA_ENV_FILE"
OWL_REQUIRED_ENV_VARS = ("VLLM_API_URL", "VLLM_MODEL_NAME", "VLLM_API_KEY")


def _candidate_env_files() -> list[Path]:
    candidates: list[Path] = []
    for key in (MAS_ENV_FILE_ENV, OWL_ENV_FILE_ENV):
        raw = os.environ.get(key)
        if raw:
            candidates.append(Path(raw).expanduser().resolve())
    return candidates


@lru_cache(maxsize=1)
def load_owl_env() -> Path | None:
    """Load OWL runtime env from explicit env-file settings if present."""
    env_files = _candidate_env_files()
    if not env_files:
        return None

    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError(
            "python-dotenv is required when MAS_FA_ENV_FILE or OWL_ENV_FILE is set."
        ) from exc

    loaded: Path | None = None
    for env_file in env_files:
        if not env_file.exists():
            raise RuntimeError(f"Configured OWL env file does not exist: {env_file}")
        load_dotenv(dotenv_path=str(env_file), override=(loaded is None))
        if loaded is None:
            loaded = env_file
    return loaded


def ensure_owl_runtime_available(*, require_api_keys: bool = False) -> None:
    """Raise a clear error if OWL/CAMEL dependencies or env are unavailable."""
    try:
        import camel  # noqa: F401
        import owl  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "OWL backend dependencies are not installed. Install them in the active "
            "environment and make sure both `camel` and `owl` are importable."
        ) from exc

    load_owl_env()

    if require_api_keys:
        missing = [key for key in OWL_REQUIRED_ENV_VARS if not os.environ.get(key)]
        if missing:
            raise RuntimeError(
                "Missing OWL runtime environment variables: "
                + ", ".join(missing)
                + ". Set them directly or point MAS_FA_ENV_FILE / OWL_ENV_FILE "
                + "to a dotenv file."
            )
