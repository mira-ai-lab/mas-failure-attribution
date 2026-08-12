"""Helpers to summarize Captain expert-group participation across rounds."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from utils.common import read_json_file, write_json_file
from utils.logging import logger

# Captain / AutoGen runtime roles — not part of the AutoBuild expert pool.
CAPTAIN_FRAMEWORK_ROLES = frozenset(
    {
        "CaptainAgent",
        "Expert_summoner",
        "speaker_selection_agent",
        "chat_manager",
        "CaptainUserProxy",
        "Captain",
    }
)

COMPUTER_TERMINAL = "Computer_terminal"

# Agents whose steps must never be chosen as attack injection targets.
NON_INJECTABLE_AGENTS = CAPTAIN_FRAMEWORK_ROLES | {COMPUTER_TERMINAL}


def history_step_agent(history: list[Any], step_id: int) -> str | None:
    """Return the agent name recorded at ``step_id`` in a task history."""
    for item in history:
        if isinstance(item, dict) and item.get("step") == step_id:
            name = item.get("name")
            return str(name) if name is not None else None
    return None


def filter_injectable_step_ids(history: list[Any]) -> list[int]:
    """Return sorted step ids whose agent is eligible for content injection."""
    step_ids: list[int] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        step = item.get("step")
        name = item.get("name")
        if not isinstance(step, int):
            continue
        if name in NON_INJECTABLE_AGENTS:
            continue
        step_ids.append(step)
    return sorted(set(step_ids))


def format_injectable_steps_for_prompt(history: list[Any], step_ids: list[int]) -> str:
    """Format injectable steps as ``step N: AgentName`` lines for the analysis prompt."""
    lines: list[str] = []
    for step_id in step_ids:
        agent = history_step_agent(history, step_id)
        if agent is None:
            continue
        lines.append(f"  step {step_id}: {agent}")
    return "\n".join(lines) if lines else "  (none)"


def validate_flip_attribution_alignment(
    pre_log: dict,
    flip_log: dict,
    info: list[dict],
    *,
    reject_framework_agents: bool = True,
) -> None:
    """Reject flip-success samples whose attribution step agent diverged on replay.

    Compares the analysis round (``pre_log``) with the flip round (``flip_log``)
    at each ``step_id`` in ``info``. Both rounds must agree on the agent name.
    When ``reject_framework_agents`` is True (attack finalization), the agent
    must also not be a framework/runtime role.
    """
    task_id = pre_log.get("question_ID") or flip_log.get("question_ID") or "<unknown>"
    pre_history = pre_log.get("history", [])
    flip_history = flip_log.get("history", [])

    for idx, item in enumerate(info):
        step = item.get("step_id")
        if not isinstance(step, int):
            raise ValueError(
                f"Invalid step_id for task {task_id}: info[{idx}].step_id={step!r}"
            )

        pre_agent = history_step_agent(pre_history, step)
        flip_agent = history_step_agent(flip_history, step)
        if pre_agent is None:
            raise ValueError(
                f"Pre-round log missing step for task {task_id}: "
                f"info[{idx}].step_id={step}"
            )
        if flip_agent is None:
            raise ValueError(
                f"Flip-round log missing step for task {task_id}: "
                f"info[{idx}].step_id={step}"
            )
        if pre_agent != flip_agent:
            raise ValueError(
                f"Replay agent mismatch for task {task_id}: info[{idx}] step {step} "
                f"pre={pre_agent!r} flip={flip_agent!r}"
            )
        if reject_framework_agents and pre_agent in NON_INJECTABLE_AGENTS:
            raise ValueError(
                f"Non-injectable agent at attribution step for task {task_id}: "
                f"info[{idx}] step {step} agent={pre_agent!r}"
            )


def validate_attack_suggestion(log: dict, suggestion: dict) -> None:
    """Reject attack suggestions targeting non-solving agents or weak content."""
    task_id = log.get("question_ID", "<unknown>")
    step = suggestion.get("step_id")
    history = log.get("history", [])
    if not isinstance(step, int):
        raise ValueError(f"Invalid step_id for task {task_id}: {step!r}")

    agent = history_step_agent(history, step)
    if agent is None:
        raise ValueError(
            f"Invalid step_id for task {task_id}: step {step} not found in history"
        )
    if agent in NON_INJECTABLE_AGENTS:
        raise ValueError(
            f"Non-injectable agent for task {task_id}: step {step} agent={agent!r}"
        )

    content = suggestion.get("attacked_content")
    if not isinstance(content, str) or len(content.strip()) < 80:
        raise ValueError(
            f"Weak attacked_content for task {task_id}: step {step} "
            f"content too short or missing"
        )

    normalized = content.strip().lower()
    meta_only_markers = (
        "i suspect",
        "might be an error",
        "without providing",
        "could lead to different",
        "higher-dimensional space",
    )
    if len(content.strip()) < 220 and any(marker in normalized for marker in meta_only_markers):
        raise ValueError(
            f"Meta-only attacked_content for task {task_id}: step {step} "
            f"lacks concrete erroneous derivation"
        )


def parse_round_index(path: Path | str) -> int | None:
    """Return round index from a path segment like ``round_1``."""
    for part in Path(path).parts:
        if part.startswith("round_") and part[6:].isdigit():
            return int(part[6:])
    return None


def resolve_round_0_library_path(workspace: Path, task_id: str) -> Path:
    """Return ``.../round_0/<task_id>`` for the current data source."""
    workspace = workspace.resolve()
    for parent in [workspace, *workspace.parents]:
        if parent.name.startswith("round_") and parent.parent.name:
            return parent.parent / "round_0" / task_id
    raise FileNotFoundError(f"cannot resolve round_0 library path from {workspace}")


def load_build_pool(library_path: Path) -> dict[str, Any]:
    """Load expert group metadata from Captain ``build_history_*.json``."""
    build_files = sorted(library_path.glob("build_history_*.json"))
    if not build_files:
        return {
            "group_name": None,
            "build_pool": [],
            "coding_enabled": False,
            "library_path": str(library_path),
            "build_history_file": None,
        }

    payload = read_json_file(build_files[-1])
    if not isinstance(payload, dict) or not payload:
        return {
            "group_name": None,
            "build_pool": [],
            "coding_enabled": False,
            "library_path": str(library_path),
            "build_history_file": str(build_files[-1]),
        }

    group_name = next(iter(payload))
    group_cfg = payload[group_name] if isinstance(payload[group_name], dict) else {}
    agent_configs = group_cfg.get("agent_configs") or []
    build_pool = [
        str(item.get("name"))
        for item in agent_configs
        if isinstance(item, dict) and item.get("name")
    ]

    return {
        "group_name": group_name,
        "build_pool": build_pool,
        "coding_enabled": bool(group_cfg.get("coding")),
        "library_path": str(library_path),
        "build_history_file": str(build_files[-1]),
    }


def collect_spoke_agents(history: list[Any]) -> dict[str, list[str]]:
    """Split history role names into experts, terminal, and framework buckets."""
    names: list[str] = []
    for item in history:
        name = getattr(item, "name", None)
        if name is None and isinstance(item, dict):
            name = item.get("name")
        if name:
            names.append(str(name))

    unique = list(dict.fromkeys(names))
    framework_roles = [name for name in unique if name in CAPTAIN_FRAMEWORK_ROLES]
    terminal_roles = [name for name in unique if name == COMPUTER_TERMINAL]
    expert_roles = [
        name
        for name in unique
        if name not in CAPTAIN_FRAMEWORK_ROLES and name != COMPUTER_TERMINAL
    ]
    return {
        "all": unique,
        "experts": expert_roles,
        "terminals": terminal_roles,
        "framework": framework_roles,
    }


def build_expert_group_summary(
    *,
    task_id: str,
    workspace: Path,
    history: list[Any],
    captain_lib_mode: str | None = None,
) -> dict[str, Any]:
    """Build per-round expert-group snapshot for ``log.json``."""
    round_index = parse_round_index(workspace)
    library_path = resolve_round_0_library_path(workspace, task_id)
    build_info = load_build_pool(library_path)
    build_pool = build_info["build_pool"]
    spoke = collect_spoke_agents(history)

    spoke_experts = [name for name in spoke["experts"] if name in build_pool]
    spoke_other_experts = [name for name in spoke["experts"] if name not in build_pool]
    silent_in_pool = [name for name in build_pool if name not in spoke_experts]

    return {
        "round": round_index,
        "task_id": task_id,
        "captain_lib_mode": captain_lib_mode,
        "expert_group": build_info["group_name"],
        "build_pool": build_pool,
        "coding_enabled": build_info["coding_enabled"],
        "library_path": build_info["library_path"],
        "spoke_experts": spoke_experts,
        "spoke_terminal": spoke["terminals"],
        "spoke_other": spoke_other_experts,
        "silent_in_pool": silent_in_pool,
        "framework_roles": spoke["framework"],
    }


def _reuse_verdict(summary: dict[str, Any]) -> dict[str, Any]:
    """Compare round 0 vs latest round for a concise pass/fail view."""
    rounds = summary.get("rounds") or {}
    if "0" not in rounds:
        return {"status": "incomplete", "reason": "round_0 missing"}

    base_pool = summary.get("baseline_build_pool")
    base_group = summary.get("baseline_expert_group")
    latest_key = max(rounds, key=lambda key: int(key))
    if latest_key == "0":
        return {
            "status": "round_0_only",
            "group_name": base_group,
            "build_pool": base_pool,
        }

    latest = rounds[latest_key]
    build_pool_match = summary.get("build_pool") == base_pool
    group_match = summary.get("expert_group") == base_group
    mode_reuse = latest.get("captain_lib_mode") == "reuse"

    ok = build_pool_match and group_match and mode_reuse
    return {
        "status": "ok" if ok else "check",
        "round_0_group": base_group,
        "latest_round": int(latest_key),
        "latest_mode": latest.get("captain_lib_mode"),
        "build_pool_unchanged": build_pool_match,
        "group_name_unchanged": group_match,
        "latest_used_reuse_mode": mode_reuse,
    }


def update_expert_group_report(
    *,
    output: Path,
    task_id: str,
    round_snapshot: dict[str, Any],
) -> Path:
    """Merge one round into ``output/<dataset>/expert_group_reports/<task_id>.json``."""
    dataset_dir = output.parent.parent
    report_dir = dataset_dir / "expert_group_reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"{task_id}.json"

    if report_path.exists():
        summary = read_json_file(report_path)
    else:
        summary = {
            "task_id": task_id,
            "expert_group": round_snapshot.get("expert_group"),
            "build_pool": round_snapshot.get("build_pool"),
            "coding_enabled": round_snapshot.get("coding_enabled"),
            "library_path": round_snapshot.get("library_path"),
            "rounds": {},
        }

    round_key = str(round_snapshot.get("round"))
    summary["rounds"][round_key] = {
        "captain_lib_mode": round_snapshot.get("captain_lib_mode"),
        "spoke_experts": round_snapshot.get("spoke_experts"),
        "spoke_terminal": round_snapshot.get("spoke_terminal"),
        "silent_in_pool": round_snapshot.get("silent_in_pool"),
        "framework_roles": round_snapshot.get("framework_roles"),
    }
    if round_key == "0":
        summary["baseline_expert_group"] = round_snapshot.get("expert_group")
        summary["baseline_build_pool"] = round_snapshot.get("build_pool")
    summary["expert_group"] = round_snapshot.get("expert_group")
    summary["build_pool"] = round_snapshot.get("build_pool")
    summary["coding_enabled"] = round_snapshot.get("coding_enabled")
    summary["library_path"] = round_snapshot.get("library_path")
    summary["reuse_verdict"] = _reuse_verdict(summary)

    write_json_file(report_path, summary)
    return report_path


def log_expert_group_summary(round_snapshot: dict[str, Any], report_path: Path) -> None:
    """Emit one human-readable expert-group line into the main run log."""
    logger.info(
        "[expert-group] task=%s round=%s mode=%s group=%r pool=%s "
        "spoke=%s silent=%s terminal=%s report=%s",
        round_snapshot.get("task_id"),
        round_snapshot.get("round"),
        round_snapshot.get("captain_lib_mode"),
        round_snapshot.get("expert_group"),
        round_snapshot.get("build_pool"),
        round_snapshot.get("spoke_experts"),
        round_snapshot.get("silent_in_pool"),
        round_snapshot.get("spoke_terminal"),
        report_path,
    )

    verdict = read_json_file(report_path).get("reuse_verdict") or {}
    if verdict.get("status") in {"ok", "check"}:
        logger.info("[reuse-verdict] status=%s detail=%s", verdict.get("status"), verdict)
