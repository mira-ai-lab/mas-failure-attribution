"""Tests for flip-round attribution alignment validation."""

import pytest

from utils.expert_group_report import validate_flip_attribution_alignment


def _log(history: list[dict]) -> dict:
    return {"question_ID": "task_test", "history": history}


def test_aligned_attack_passes():
    pre = _log([{"step": 1, "name": "Algebra_Expert"}, {"step": 2, "name": "Algebra_Expert"}])
    flip = _log([{"step": 1, "name": "Algebra_Expert"}, {"step": 2, "name": "Algebra_Expert"}])
    info = [{"step_id": 2, "attacked_content": "x" * 80, "related_error": []}]
    validate_flip_attribution_alignment(pre, flip, info)


def test_replay_agent_mismatch_rejected():
    pre = _log([{"step": 1, "name": "Algebra_Expert"}, {"step": 2, "name": "Algebra_Expert"}])
    flip = _log(
        [
            {"step": 1, "name": "Algebra_Expert"},
            {"step": 2, "name": "speaker_selection_agent"},
        ]
    )
    info = [{"step_id": 2, "attacked_content": "x" * 80, "related_error": []}]
    with pytest.raises(ValueError, match="Replay agent mismatch"):
        validate_flip_attribution_alignment(pre, flip, info)


def test_framework_agent_rejected():
    pre = _log([{"step": 1, "name": "speaker_selection_agent"}])
    flip = _log([{"step": 1, "name": "speaker_selection_agent"}])
    info = [{"step_id": 1, "related_error": []}]
    with pytest.raises(ValueError, match="Non-injectable agent"):
        validate_flip_attribution_alignment(pre, flip, info)


def test_diagnose_mode_allows_framework_when_pre_matches_flip():
    pre = _log([{"step": 1, "name": "speaker_selection_agent"}])
    flip = _log([{"step": 1, "name": "speaker_selection_agent"}])
    info = [{"step_id": 1, "related_error": []}]
    validate_flip_attribution_alignment(
        pre, flip, info, reject_framework_agents=False
    )
