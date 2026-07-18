"""Task runners organized by execution paradigm."""

from pipeline.runners.code_generation import run_code_generation_task
from pipeline.runners.text_answer import run_text_answer_task
from pipeline.runners.web_research import run_web_research_task

__all__ = [
    "run_code_generation_task",
    "run_text_answer_task",
    "run_web_research_task",
]
