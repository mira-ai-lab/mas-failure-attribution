"""MetaGPT backend adapter that instruments roles with monitoring middlewares."""

import asyncio
from pathlib import Path

from adapter.MetaGPT.middlewares.observe import ObserveMiddleware
from adapter.MetaGPT.middlewares.think import ThinkMiddleware
from adapter.middleware import patch_with_middlewares
from monitor.base_monitor import BaseMonitor
from utils.prompts import REPLAY_PROMPT

from ..base_adapter import BaseAdapter
from metagpt.prompts.di.team_leader import TL_INSTRUCTION
from metagpt.prompts.di.architect import ARCHITECT_INSTRUCTION
from metagpt.prompts.di.engineer2 import WRITE_CODE_SYSTEM_PROMPT
from metagpt.prompts.product_manager import EXTRA_INSTRUCTION as PRODUCT_MANAGER_INSTRUCTION
from metagpt.prompts.di.data_analyst import EXTRA_INSTRUCTION as DATA_ANALYST_INSTRUCTION
from metagpt.config2 import config
from metagpt.context import Context
from metagpt.team import Team
from metagpt.roles import (
    Architect,
    DataAnalyst,
    Engineer2,
    ProductManager,
    TeamLeader,
    Searcher,
)
from metagpt.logs import set_human_input_func
from metagpt.environment.mgx.mgx_env import MGXEnv
from utils.logging import logger

class SerialMGXEnv(MGXEnv):
    """保留 MGX 消息链，仅将一轮内的多角色从并发改为串行。"""
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

        # Initialize the Team
        self.company = Team(context=ctx, env=SerialMGXEnv())
        leader = TeamLeader()
        self.llm = leader.llm
        members = [
            leader,
            ProductManager(),
            Architect(),
            Engineer2(),
            DataAnalyst(),
            #Searcher(),
        ]
        self.company.hire(members)
        
        # insert monitor into MAS
        for member in members:
            if hasattr(member, "editor"):            
                member.editor.working_dir = workspace
                member.editor.enable_auto_lint = False
            patch_with_middlewares(
                member,
                "_observe",
                [ObserveMiddleware(monitor)]
            )

            if hasattr(member, 'llm'):
                patch_with_middlewares(
                    member.llm,
                    "aask",
                    [ThinkMiddleware(monitor, member.profile)]
                )
            
            if hasattr(member, 'terminal'):
                member.terminal.work_dir = workspace

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
        task_id: str | None = None,
    ):
        """Execute backend task using adapter defaults for round count and setup."""
        return await self.generate_repo(
            idea=idea,
            n_round=5,
            recover_path=recovery,
            workspace=workspace,
            monitor=monitor,
            use_async=True
        )
    


    def get_prompt_map(self):
        """Expose role system prompts for downstream logging and analysis."""
        return {
            "Team Leader": TL_INSTRUCTION,
            "Engineer": WRITE_CODE_SYSTEM_PROMPT,
            "Architect": ARCHITECT_INSTRUCTION,
            "Product Manager": PRODUCT_MANAGER_INSTRUCTION,
            "DataAnalyst": DATA_ANALYST_INSTRUCTION,
            "Searcher": '',
        }

    async def llm_aask(self, msg:str):
        return await self.llm.aask(msg)
    