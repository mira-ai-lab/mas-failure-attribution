"""Abstract backend adapter contract for MAS execution engines."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict


class BaseAdapter(ABC):
    """Define the minimal interface required by the pipeline runtime."""

    @abstractmethod
    async def run_backend(
        self,
        idea: str,
        workspace: Path,
        recovery: Path = None,
        monitor = None,
        task_id: str | None = None,
    ):
        """Execute a task idea inside a workspace with optional recovery/monitoring."""
        pass

    @abstractmethod
    def get_prompt_map(self) -> Dict[str, str]:
        """Return role-to-system-prompt mapping used for experiment logging."""
        pass

    def get_trace_log(self) -> Dict[str, object] | None:
        """Return optional structured trace data for log.json generation."""
        return None