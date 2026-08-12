"""Shared meta-language leakage detection for attack attribution samples."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# Informational only — NOT counted as dataset leakage by default.
ATTACKED_CONTENT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"introduce\s+(?:a\s+)?(?:subtle\s+)?(?:error|miscalculation)", "introduce_error"),
    (r"modify\s+the", "modify_the"),
    (r"introduce\s+a\s+", "introduce_a"),
    (r"per\s+instructions?", "per_instructions"),
    (r"\binjection\b", "injection"),
    (r"mistakenly\s+stated", "mistakenly_stated"),
    (r"guidance\s+only", "guidance_only"),
    (r"change\s+the\s+line", "change_the_line"),
    (r"subtle\s+(?:error|miscalculation|reasoning\s+error)", "subtle_error"),
    (r"instead\s+of\s+(?:the\s+)?correct", "instead_of_correct"),
    (r"as\s+requested", "as_requested"),
    (r"in\s+step\s+\d+,\s+the\s+", "in_step_the"),
    (r"in\s+step\s+\d+,", "in_step"),
    (r"\bincorrectly\s+(?:calculate|calculates|compute|computes|simplify|simplifies|state|states|extract|extracts)\b", "incorrectly_verb"),
    (r"我将", "zh_will"),
    (r"注入", "zh_inject"),
    (r"按照.*修改", "zh_modify_per"),
    (r"引入错误", "zh_introduce_error"),
    (r"错误地", "zh_wrongly"),
)

# Injection-specific phrases in replayed ``history[].content``.
# Deliberately excludes generic post-hoc summaries like "the expert incorrectly stated …"
# inside Expert_summoner reports — those are not injection shortcut labels.
HISTORY_INJECTION_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"modified\s+per\s+instructions?", "modified_per_instructions"),
    (r"with\s+the\s+incorrect\s+\w+\s+specified", "incorrect_addition_specified"),
    (r"from\s+the\s+injection", "from_the_injection"),
    (r"following\s+the\s+injection", "following_the_injection"),
    (r"following\s+the\s+modification\s+in\s+the\s+injection\s+info", "following_modification_injection_info"),
    (r"as\s+per\s+the\s+injection", "as_per_the_injection"),
    (r"per\s+the\s+injection", "per_the_injection"),
    (r"reflect\s+the\s+injection", "reflect_the_injection"),
    (r"as\s+per\s+the\s+instruction", "as_per_the_instruction"),
    (r"\(as\s+per\s+the\s+instruction\)", "paren_as_per_instruction"),
    (r"\bINJECTION\s+INFO\b", "INJECTION_INFO"),
    (r"\binjection\s+info\b", "injection_info"),
    (r"the\s+instructions\s+specify", "instructions_specify"),
    (r"however,?\s+the\s+instructions\s+specify", "however_instructions_specify"),
    (r"incorrectly\s+stated,?\s+as\s+it\s+should", "incorrectly_stated_as_should"),
    (r"incorrectly\s+adding", "incorrectly_adding"),
    (r"injected\s+adjustment", "injected_adjustment"),
    (r"with\s+the\s+injected\s+adjustment", "with_injected_adjustment"),
    (r"按照.*修改", "zh_modify_per"),
    (r"按照要求", "zh_as_requested"),
    (r"注入", "zh_inject"),
)

# Legacy alias kept for scripts that imported HISTORY_PATTERNS.
HISTORY_PATTERNS = HISTORY_INJECTION_PATTERNS


@dataclass
class LeakageHit:
    """One detected leakage instance."""

    category: str  # "attacked_content" | "history"
    step: int | None = None
    agent: str | None = None
    markers: list[str] = field(default_factory=list)
    snippet: str = ""


@dataclass
class SampleAuditResult:
    """Audit result for one final_results JSON file."""

    task_id: str
    source_run: str
    source_path: str
    sample_type: str  # "attack" | "diagnose" | "unknown"
    attacked_content_leak: bool = False
    history_leak: bool = False
    any_leak: bool = False
    attacked_content_hits: list[str] = field(default_factory=list)
    history_hits: list[str] = field(default_factory=list)
    history_hit_details: list[LeakageHit] = field(default_factory=list)
    attacked_content_preview: str = ""
    inject_step_id: int | None = None


def _match_patterns(text: str, patterns: Iterable[tuple[str, str]]) -> list[str]:
    if not text:
        return []
    hits: list[str] = []
    for pattern, name in patterns:
        if re.search(pattern, str(text), flags=re.IGNORECASE):
            hits.append(name)
    return hits


def _sample_type(mistake_information: list[Any]) -> str:
    if not mistake_information:
        return "unknown"
    if any(isinstance(item, dict) and "attacked_content" in item for item in mistake_information):
        return "attack"
    if any(isinstance(item, dict) and "suggested_fix" in item for item in mistake_information):
        return "diagnose"
    return "unknown"


def audit_final_result(data: dict[str, Any], *, source_run: str, source_path: str) -> SampleAuditResult:
    """Audit one final_results payload for meta-language leakage.

    Leakage definition (strict):
    - Only ``history[].content`` matches against injection-specific patterns count.
    - ``attacked_content`` matches are recorded for reference but do **not** set ``any_leak``.
    - The full history trajectory is scanned (leaks may appear before ``inject_step_id`` when
      an earlier step was rewritten during replay).
    """
    task_id = str(data.get("question_ID") or data.get("task_id") or "unknown")
    mi = data.get("mistake_information") or []
    sample_type = _sample_type(mi)

    result = SampleAuditResult(
        task_id=task_id,
        source_run=source_run,
        source_path=source_path,
        sample_type=sample_type,
    )

    if sample_type != "attack" or not mi:
        return result

    last = mi[-1] if isinstance(mi[-1], dict) else {}
    attacked_content = str(last.get("attacked_content") or "")
    result.inject_step_id = last.get("step_id") if isinstance(last.get("step_id"), int) else None
    result.attacked_content_preview = attacked_content[:200]

    ac_hits = _match_patterns(attacked_content, ATTACKED_CONTENT_PATTERNS)
    if ac_hits:
        result.attacked_content_leak = True
        result.attacked_content_hits = ac_hits

    history = data.get("history") or []
    all_history_hits: list[str] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "")
        step_hits = _match_patterns(content, HISTORY_INJECTION_PATTERNS)
        if not step_hits:
            continue
        all_history_hits.extend(step_hits)
        result.history_hit_details.append(
            LeakageHit(
                category="history",
                step=item.get("step") if isinstance(item.get("step"), int) else None,
                agent=str(item.get("name") or ""),
                markers=step_hits,
                snippet=content[:240],
            )
        )

    if all_history_hits:
        result.history_leak = True
        result.history_hits = sorted(set(all_history_hits))

    result.any_leak = result.history_leak
    return result


def audit_output_dirs(output_dirs: list[Path]) -> list[SampleAuditResult]:
    """Audit all ``final_results/*.json`` under the given run directories."""
    results: list[SampleAuditResult] = []
    for run_dir in output_dirs:
        run_dir = run_dir.resolve()
        final_dir = run_dir / "final_results"
        if not final_dir.is_dir():
            continue
        for path in sorted(final_dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            results.append(
                audit_final_result(
                    data,
                    source_run=run_dir.name,
                    source_path=str(path),
                )
            )
    return results


def summarize_audit(results: list[SampleAuditResult]) -> dict[str, Any]:
    """Build summary statistics from per-sample audit results."""
    total = len(results)
    attacks = [r for r in results if r.sample_type == "attack"]
    diagnoses = [r for r in results if r.sample_type == "diagnose"]
    leaked = [r for r in attacks if r.history_leak]
    ac_only = [r for r in attacks if r.attacked_content_leak and not r.history_leak]

    by_run: dict[str, dict[str, int]] = {}
    for r in results:
        bucket = by_run.setdefault(
            r.source_run,
            {"total": 0, "attack": 0, "diagnose": 0, "leaked": 0, "ac_only": 0},
        )
        bucket["total"] += 1
        if r.sample_type == "attack":
            bucket["attack"] += 1
            if r.history_leak:
                bucket["leaked"] += 1
            elif r.attacked_content_leak:
                bucket["ac_only"] += 1
        elif r.sample_type == "diagnose":
            bucket["diagnose"] += 1

    marker_counts: dict[str, int] = {}
    for r in attacks:
        for m in r.history_hits:
            marker_counts[m] = marker_counts.get(m, 0) + 1

    return {
        "leak_definition": "history_injection_only",
        "total_final_results": total,
        "attack_count": len(attacks),
        "diagnose_count": len(diagnoses),
        "leaked_attack_count": len(leaked),
        "leaked_attack_rate": len(leaked) / len(attacks) if attacks else 0.0,
        "attacked_content_only_not_counted": len(ac_only),
        "history_leak_count": len(leaked),
        "by_run": by_run,
        "top_history_markers": sorted(marker_counts.items(), key=lambda x: (-x[1], x[0])),
    }
