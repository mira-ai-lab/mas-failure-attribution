"""Injection-aware monitor used during replay and attribution rounds."""

from pathlib import Path
from typing import Any

from pydantic import PrivateAttr

from adapter.base_adapter import BaseAdapter
from monitor.base_monitor import BaseMonitor
from utils.common import read_json_file
from utils.logging import logger
from utils.prompts import REPLAY_PROMPT

class AttackMonitor(BaseMonitor):
    """Monitor variant that injects a single planned replay modification."""

    _attack_step: int = PrivateAttr()
    _attack_suggestion: str = PrivateAttr()
    _last_round_log: dict = PrivateAttr()
    _injected: bool = PrivateAttr(default=False)

    def __init__(self, recovery: Path, workspace:Path, backend: BaseAdapter, suggestion: dict, last_round_log: dict, **data: Any):
        """Initialize attack monitor from suggestion payload and base metadata."""
        super().__init__(recovery, workspace, backend, **data)
        self._attack_step = suggestion['step_id']
        if 'attacked_content' in suggestion:
            self._attack_suggestion = suggestion['attacked_content']
        elif 'suggested_fix' in suggestion:
            self._attack_suggestion = suggestion['suggested_fix']
        else:
            raise KeyError('not found key in suggestion')
        self._last_round_log = last_round_log

    @classmethod
    def deserialize(
        cls, 
        stg_path: Path, 
        recovery: Path, 
        workspace: Path, 
        backend: BaseAdapter,
        suggestion: dict,
    ) -> "AttackMonitor":
        """Deserialize monitor state and bind the current injection suggestion."""
        monitor_info_path = stg_path.joinpath("monitor.json")
        if not monitor_info_path.exists():
            raise FileNotFoundError(
                "recover storage meta file `` not exist, " "not to recover and please start a new project."
            )

        monitor_info: dict = read_json_file(monitor_info_path)
        monitor = AttackMonitor(
            recovery=recovery, 
            workspace=workspace, 
            backend=backend, 
            suggestion=suggestion,
            last_round_log=None
            **monitor_info
        )
        return monitor

    def inject_content(self, default_value: str) -> str:
        if self.should_inject():
            logger.info("Injection step detected, injecting...")
            self._injected = True
            if not (default_value or "").strip():
                # default_value should be the agent's system prompt (persona) when injecting
                # via the system channel (see observe.py::_inject_replay_prompt_system).
                # Do NOT fall back to the previous round's step content: that content carries
                # the previous round's (possibly correct) answer into ORIGINAL_TASK, which
                # makes the LLM reproduce the correct answer and defeats the injection.
                logger.error(
                    "[inject-debug] original_task empty on inject (step=%s); expected the "
                    "agent system prompt as default_value. Injecting with empty ORIGINAL_TASK.",
                    self.step,
                )
            return REPLAY_PROMPT.format(
                original_task=default_value,
                injection_info=self._attack_suggestion,
            )
        return default_value

    def get_current_reply(self) -> str:
        return self._last_round_log['history'][self.step-1]['content']

    def should_inject(self):
        """Return True when current step matches configured injection step."""
        return self.step == self._attack_step and not self._injected

    @property
    def attack_step(self) -> int:
        """Expose configured attack step for middleware branching."""
        return self._attack_step
    
    def is_injected(self) -> bool:
        return self._injected