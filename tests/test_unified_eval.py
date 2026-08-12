from __future__ import annotations

import asyncio
import json
from pathlib import Path

from pipeline.eval import core
from pipeline.eval.registry import EvaluatorSpec, resolve_strategy
from pipeline.eval.scorers.rule_based import gaia_score
from pipeline.math.eval import extract_boxed_answer, math_eval, normalize_boxed_answer
from pipeline.eval.types import EvalOutcome


def _write_task_log(base: Path, task_id: str, **payload) -> None:
    task_dir = base / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    log = {
        "question_ID": task_id,
        "question": payload.get("question", ""),
        "ground_truth": payload.get("ground_truth", ""),
        "model_prediction": payload.get("model_prediction", ""),
        "test": payload.get("test", ""),
    }
    (task_dir / "log.json").write_text(json.dumps(log), encoding="utf-8")


def test_resolve_strategy() -> None:
    assert resolve_strategy("OWL", "kodcode") == "sandbox"
    assert resolve_strategy("OWL", "gaia") == "rule_based"
    assert resolve_strategy("OWL", "browsecomp") == "llm_judge"
    assert resolve_strategy("Captain", "math") == "math_boxed"
    assert resolve_strategy("OWL", "some_new_dataset") == "llm_judge"
    assert resolve_strategy("MagenticOne", "gaia") == "universal"


def test_extract_boxed_answer_balanced_braces() -> None:
    assert extract_boxed_answer(r"Therefore $a=\boxed{\frac{1}{2}}$.") == r"\frac{1}{2}"
    assert extract_boxed_answer(r"First \boxed{3}, final \boxed{4}") == "4"
    assert extract_boxed_answer("no boxed here") == ""
    assert extract_boxed_answer(r"Empty answer \boxed{}") == ""


def test_math_eval_uses_last_boxed_answer() -> None:
    truth = r"Work shown. Final answer is \boxed{\frac{1}{2}}."
    assert math_eval(r"\boxed{\frac{1}{2}}", truth)
    assert not math_eval(r"\boxed{3} and later \boxed{4}", r"Expected \boxed{3}")
    assert not math_eval(r"\boxed{2}", truth)


def test_normalize_boxed_answer() -> None:
    assert normalize_boxed_answer("{45}") == "45"
    assert normalize_boxed_answer("{-30}") == "-30"
    assert normalize_boxed_answer("11.0") == "11"
    assert normalize_boxed_answer(r"\frac{1}{2}") == r"\frac{1}{2}"
    assert normalize_boxed_answer("14.67") == "14.67"


def test_math_eval_tolerates_format_variants() -> None:
    truth = r"Final answer is \boxed{11}."
    assert math_eval(r"\boxed{11.0}", truth)
    assert math_eval(r"\boxed{{45}}", r"Expected \boxed{45}")
    assert math_eval(r"\boxed{{-30}}", r"Expected \boxed{-30}")
    assert not math_eval(r"\boxed{14.67}", truth)


def test_gaia_score_normalization() -> None:
    assert gaia_score(" $1,234 ", "1234")
    assert gaia_score("alpha, beta", "alpha, beta")
    assert gaia_score("sea gull", "seagull")
    assert not gaia_score("alpha, gamma", "alpha, beta")


def test_load_eval_results_without_message_file(tmp_path: Path) -> None:
    eval_path = tmp_path / "round_0"
    eval_path.mkdir(parents=True, exist_ok=True)
    (eval_path / "eval_demo.json").write_text(json.dumps({"t1": True}), encoding="utf-8")

    status, messages = core.load_eval_results(eval_path, "demo")

    assert status == {"t1": True}
    assert messages == {"t1": ""}


def test_evaluate_round_writes_status_and_messages(tmp_path: Path, monkeypatch) -> None:
    eval_path = tmp_path / "round_0"
    eval_path.mkdir(parents=True, exist_ok=True)
    _write_task_log(eval_path, "pass_task", question="pass")
    _write_task_log(eval_path, "fail_task", question="fail")

    async def fake_evaluate_task(task: dict, *, semaphore=None) -> EvalOutcome:
        del semaphore
        passed = task["question"] == "pass"
        return EvalOutcome(task_id=task["question_ID"], passed=passed, message=f"msg:{task['question']}")

    monkeypatch.setattr(
        core,
        "resolve_evaluator",
        lambda backend, data_source: EvaluatorSpec(strategy="fake", evaluate_task=fake_evaluate_task),
    )

    status, messages = asyncio.run(
        core.evaluate_round(eval_path, "demo", backend="OWL", skip_existing=False)
    )

    assert status == {"fail_task": False, "pass_task": True}
    assert messages == {"fail_task": "msg:fail", "pass_task": "msg:pass"}
    assert json.loads((eval_path / "eval_demo.json").read_text(encoding="utf-8")) == status
    assert json.loads((eval_path / "eval_msg_demo.json").read_text(encoding="utf-8")) == messages
