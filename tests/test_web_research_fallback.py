from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path


def _install_import_stubs() -> None:
    adapter_pkg = types.ModuleType("adapter")
    base_adapter_mod = types.ModuleType("adapter.base_adapter")

    class BaseAdapter:
        pass

    base_adapter_mod.BaseAdapter = BaseAdapter
    adapter_pkg.base_adapter = base_adapter_mod

    monitor_pkg = types.ModuleType("monitor")
    base_monitor_mod = types.ModuleType("monitor.base_monitor")

    class BaseMonitor:
        history: list = []

    base_monitor_mod.BaseMonitor = BaseMonitor
    monitor_pkg.base_monitor = base_monitor_mod

    schema_mod = types.ModuleType("model.schema")

    class History:
        def __init__(self, step, content, role, name):
            self.step = step
            self.content = content
            self.role = role
            self.name = name

    schema_mod.History = History

    expert_mod = types.ModuleType("utils.expert_group_report")
    expert_mod.CAPTAIN_FRAMEWORK_ROLES = frozenset({"Captain", "Expert_summoner"})
    expert_mod.COMPUTER_TERMINAL = "Computer_terminal"

    common_mod = types.ModuleType("utils.common")
    common_mod.dumps = lambda value: value

    logging_mod = types.ModuleType("utils.logging")

    class Logger:
        def info(self, *args, **kwargs) -> None:
            return None

        def warning(self, *args, **kwargs) -> None:
            return None

    logging_mod.logger = Logger()

    for name, module in {
        "adapter": adapter_pkg,
        "adapter.base_adapter": base_adapter_mod,
        "monitor": monitor_pkg,
        "monitor.base_monitor": base_monitor_mod,
        "model.schema": schema_mod,
        "utils.expert_group_report": expert_mod,
        "utils.common": common_mod,
        "utils.logging": logging_mod,
    }.items():
        sys.modules[name] = module


_install_import_stubs()

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "web_research_runner",
    ROOT / "pipeline" / "runners" / "web_research.py",
)
web_research = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(web_research)


class _DummyBackend:
    pass


def test_normalize_web_research_answer_strips_final_answer_prefix() -> None:
    assert web_research._normalize_web_research_answer("FINAL ANSWER: 42") == "42"
    assert (
        web_research._normalize_web_research_answer("Analysis...\nFINAL ANSWER: egalitarian")
        == "egalitarian"
    )
    assert web_research._normalize_web_research_answer("<final_answer>34689</final_answer>") == "34689"


def test_extract_web_research_prediction_prefers_backend_summary() -> None:
    class Result:
        summary = "FINAL ANSWER: 1234"

    prediction = web_research._extract_web_research_prediction(Result(), _DummyBackend(), [])
    assert prediction == "1234"


def test_extract_web_research_prediction_falls_back_to_history() -> None:
    history = [
        sys.modules["model.schema"].History(
            step=1, content="Working...", role="Assistant", name="Captain"
        ),
        sys.modules["model.schema"].History(
            step=2,
            content="FINAL ANSWER: alpha, beta",
            role="Assistant",
            name="Logic_Expert",
        ),
    ]
    prediction = web_research._extract_web_research_prediction(None, _DummyBackend(), history)
    assert prediction == "alpha, beta"


def test_resolve_model_prediction_uses_existing_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "final_answer.txt").write_text("from-file", encoding="utf-8")

    prediction = web_research._resolve_model_prediction(
        workspace,
        result=None,
        backend=_DummyBackend(),
        history=[],
        data_source="gaia",
        task_id="task-1",
    )
    assert prediction == "from-file"


def test_resolve_model_prediction_backfills_missing_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class Result:
        summary = "FINAL ANSWER: backfilled"

    prediction = web_research._resolve_model_prediction(
        workspace,
        result=Result(),
        backend=_DummyBackend(),
        history=[],
        data_source="gaia",
        task_id="task-2",
    )
    assert prediction == "backfilled"
    assert (workspace / "final_answer.txt").read_text(encoding="utf-8") == "backfilled"
