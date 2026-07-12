"""Abstract backend adapter contract for MAS execution engines."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional


class BaseAdapter(ABC):
    """Define the minimal interface required by the pipeline runtime."""

    @abstractmethod
    async def run_backend(
        self,
        idea: str,
        workspace: Path,
        recovery: Path = None,
        monitor = None,
        enable_lint: bool = True,
        task: Optional[Dict[str, Any]] = None,
    ):
        """Execute a task idea inside a workspace with optional recovery/monitoring."""
        pass

    @abstractmethod
    def save_current_state(
        self,
        path: Path
    ):
        """Persist current backend runtime state for later recovery."""
        pass

    @abstractmethod
    def get_prompt_map(self) -> Dict[str, str]:
        """Return role-to-system-prompt mapping used for experiment logging."""
        pass