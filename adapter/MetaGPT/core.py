"""MetaGPT backend adapter that instruments roles with monitoring middlewares."""

import asyncio
from pathlib import Path

from adapter.MetaGPT.middlewares.engineer import EngineerMiddleware
from adapter.MetaGPT.middlewares.observe import ObserveMiddleware
from adapter.MetaGPT.middlewares.team_leader import TeamLeaderMiddleware
from adapter.MetaGPT.middlewares.terminal import TerminalMiddleware
from adapter.MetaGPT.middlewares.think import ThinkMiddleware
from adapter.MetaGPT.utils import get_name
from adapter.middleware import patch_with_middlewares
from monitor.base_monitor import BaseMonitor, RoleType
from utils.prompts import REPLAY_PROMPT

from ..base_adapter import BaseAdapter
from metagpt.prompts.di.team_leader import TL_INSTRUCTION
from metagpt.prompts.di.architect import ARCHITECT_INSTRUCTION
from metagpt.prompts.di.engineer2 import WRITE_CODE_SYSTEM_PROMPT
from metagpt.prompts.product_manager import EXTRA_INSTRUCTION as PRODUCT_MANAGER_INSTRUCTION
from metagpt.prompts.di.data_analyst import EXTRA_INSTRUCTION as DATA_ANALYST_INSTRUCTION
from metagpt.config2 import config
from metagpt.context import Context
from metagpt.team import Message, Team
from metagpt.roles import (
    Architect,
    DataAnalyst,
    Engineer2,
    ProductManager,
    TeamLeader,
)
from metagpt.logs import set_human_input_func
from metagpt.environment.mgx.mgx_env import MGXEnv
from utils.logging import logger

class SerialMGXEnv(MGXEnv):
    """Keep the MGX message chain; run each role sequentially within a round instead of concurrently."""
    async def run(self, k=1):
        for _ in range(k):
            for role in self.roles.values():
                if role.is_idle:
                    continue
                await role.run()
            logger.debug(f"is idle: {self.is_idle}")

DEFAULT_HUMAN_REPLY = (
    "Proceed without the system design document. "
    "Implement the solution based on the task requirement only."
)

def install_human_input_autoreply(reply: str = None) -> None:
    """Install deterministic auto-reply for any human-input callbacks."""
    if reply is None:
        reply = get_default_human_reply()
    set_human_input_func(lambda _: reply)

class MetaGPTAdapter(BaseAdapter):
    """Adapter implementation that runs tasks with MetaGPT team orchestration."""

    def generate_repo(
        self,
        idea: str,
        investment: float = 3.0,
        n_round: int = 5,
        code_review: bool = True,
        run_tests: bool = False,
        implement: bool = True,
        project_name: str = "",
        inc: bool = False,
        project_path: str = "",
        workspace: Path = None,
        reqa_file: str = "",
        max_auto_summarize_code: int =0,
        recover_path: Path = None,
        monitor: BaseMonitor = None,
        use_async: bool = False,
        enable_lint: bool = True,
    ):
        """Run or resume a MetaGPT team and execute the given task instruction."""
        
        async def _safe_close_ctx_llm():
            """Close LLM client gracefully to avoid resource leaks."""
            llm = getattr(ctx, "_llm", None)
            if llm is None:
                return
            try:
                await llm.aclose()
            except Exception:
                pass
        
        install_human_input_autoreply(DEFAULT_HUMAN_REPLY)
        config.update_via_cli(project_path, project_name, inc, reqa_file, max_auto_summarize_code)
        ctx = Context(config=config)

        # resume MetaGPT MAS
        if recover_path:
            if not recover_path.exists():
                raise FileNotFoundError(f"{recover_path} not exists")
            self.company = Team.deserialize(stg_path=recover_path, context=ctx)
            members = [role for role in self.company.env.get_roles().values()]

            # awake current working agent 
            if len(monitor.history) > 0:
                involved_role_names = [get_name(step.name) for step in monitor.history]
                involved_roles = [self.company.env.get_role(name) for name in involved_role_names]
                for role in involved_roles:
                    role.put_message(Message(
                        content=f'Continue to process: {idea}', 
                        send_to={role.name}
                    ))
        else:
            # Initialize the Team
            self.company = Team(context=ctx, env=SerialMGXEnv())
            members = [
                TeamLeader(),
                ProductManager(),
                Architect(),
                Engineer2(),
                DataAnalyst()
            ]
            self.company.hire(members)
        
        # insert monitor into MAS
        for member in members:
            member.editor.working_dir = workspace
            member.editor.enable_auto_lint = False
            if hasattr(member, 'llm'):
                patch_with_middlewares(
                    member.llm,
                    "aask",
                    [ThinkMiddleware(monitor, member.profile)]
                )
            patch_with_middlewares(
                member,
                "_observe",
                [ObserveMiddleware(monitor)]
            )
            
            if hasattr(member, 'terminal'):
                member.terminal.work_dir = workspace
                '''patch_with_middlewares(
                    member.terminal,
                    "_read_and_process_output",
                    [TerminalMiddleware(monitor)]
                )
            if hasattr(member, "write_new_code"):
                patch_with_middlewares(
                    member,
                    "write_new_code",
                    [EngineerMiddleware(monitor)]
                )
            if hasattr(member, "write_and_exec_code"):
                patch_with_middlewares(
                    member,
                    "write_and_exec_code",
                    [EngineerMiddleware(monitor)]
                )
            if hasattr(member, "publish_team_message"):
                patch_with_middlewares(
                    member,
                    "publish_team_message",
                    [TeamLeaderMiddleware(monitor)]
                )
            '''
        self.company.invest(investment)
        coro = self.company.run(n_round=n_round, idea=idea)
        if use_async:
            async def _run_and_cleanup():
                try:
                    return await coro
                finally:
                    await _safe_close_ctx_llm()

            return _run_and_cleanup()
        else:
            asyncio.run(coro)
            asyncio.run(_safe_close_ctx_llm())
            return ctx.kwargs.get("project_path")
    
    async def run_backend(
        self,
        idea: str,
        workspace: Path,
        recovery: Path = None,
        monitor: BaseMonitor = None,
        enable_lint: bool = True,
        task=None,
    ):
        """Execute backend task using adapter defaults for round count and setup."""
        del task  # Dataset row not used by MetaGPT adapter.
        return self.generate_repo(
            idea=idea,
            n_round=20,
            recover_path=recovery,
            workspace=workspace,
            monitor=monitor,
            enable_lint=enable_lint,
        )
    
    def save_current_state(self, path: Path):
        """Serialize current MetaGPT team state to recovery directory."""
        self.company.serialize(path)

    def get_prompt_map(self):
        """Expose role system prompts for downstream logging and analysis."""
        return {
            "Team Leader": TL_INSTRUCTION,
            "Engineer": WRITE_CODE_SYSTEM_PROMPT,
            "Architect": ARCHITECT_INSTRUCTION,
            "Product Manager": PRODUCT_MANAGER_INSTRUCTION,
            "DataAnalyst": DATA_ANALYST_INSTRUCTION,
        }
    