"""Runtime config helpers for MetaGPT adapter initialization."""

import os
from pathlib import Path

from utils.logging import logger


def prepare_metagpt_config() -> None:
    """Ensure ~/.metagpt/config2.yaml is available for MetaGPT runtime.

    Priority:
    1. METAGPT_CONFIG2_PATH: copy pointed file to ~/.metagpt/config2.yaml.
    2. Build a minimal config from env vars:
       - METAGPT_API_KEY / OPENAI_API_KEY / VLLM_API_KEY
       - METAGPT_BASE_URL / OPENAI_API_BASE_URL / VLLM_API_URL
       - METAGPT_MODEL / OPENAI_MODEL / VLLM_MODEL_NAME
    """

    config_root = Path.home() / ".metagpt"
    target = config_root / "config2.yaml"
    config_root.mkdir(parents=True, exist_ok=True)

    config2_path = os.getenv("METAGPT_CONFIG2_PATH", "").strip()
    if config2_path:
        source = Path(config2_path).expanduser().resolve()
        if not source.exists():
            raise FileNotFoundError(f"METAGPT_CONFIG2_PATH does not exist: {source}")
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        logger.info("Prepared MetaGPT config from METAGPT_CONFIG2_PATH: %s", source)
        return

    api_key = (
        os.getenv("METAGPT_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("VLLM_API_KEY")
        or ""
    ).strip()
    if not api_key:
        logger.warning(
            "MetaGPT config is missing. Provide METAGPT_CONFIG2_PATH or one of "
            "METAGPT_API_KEY/OPENAI_API_KEY/VLLM_API_KEY in env_file."
        )
        return

    base_url = (
        os.getenv("METAGPT_BASE_URL")
        or os.getenv("OPENAI_API_BASE_URL")
        or os.getenv("VLLM_API_URL")
        or "https://api.openai.com/v1"
    ).strip()
    model = (
        os.getenv("METAGPT_MODEL")
        or os.getenv("OPENAI_MODEL")
        or os.getenv("VLLM_MODEL_NAME")
        or "gpt-4o-mini"
    ).strip()

    yaml_text = (
        "llm:\n"
        "  api_type: openai\n"
        f"  api_key: {api_key}\n"
        f"  base_url: {base_url}\n"
        f"  model: {model}\n"
        "  temperature: 0.0\n"
    )
    target.write_text(yaml_text, encoding="utf-8")
    logger.info("Prepared minimal MetaGPT config at %s", target)


def prepare_runtime_config() -> None:
    """Generic runtime bootstrap hook used by adapter.runtime_bootstrap."""

    prepare_metagpt_config()
