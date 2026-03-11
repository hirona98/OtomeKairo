"""Deterministic golden-pack verification for chat behavior."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from otomekairo.usecase.chat_replay_eval import build_chat_replay_eval_report
from otomekairo.usecase.memory_write_e2e import run_memory_write_e2e


# Block: Report constants
REPORT_SCHEMA_VERSION = 2


# Block: Public report build
def build_chat_behavior_golden_report(*, keep_db: bool) -> dict[str, Any]:
    temp_dir = Path(tempfile.mkdtemp(prefix="otomekairo-chat-behavior-golden-"))
    db_path = temp_dir / "core.sqlite3"
    try:
        memory_report = run_memory_write_e2e(db_path=db_path)
        chat_report = build_chat_replay_eval_report(
            db_path=db_path,
            limit=20,
        )
        report = _build_report(
            db_path=db_path,
            keep_db=keep_db,
            memory_report=memory_report,
            chat_report=chat_report,
        )
        _validate_report(report)
        return report
    finally:
        if not keep_db:
            shutil.rmtree(temp_dir, ignore_errors=True)


# Block: Report build
def _build_report(
    *,
    db_path: Path,
    keep_db: bool,
    memory_report: dict[str, Any],
    chat_report: dict[str, Any],
) -> dict[str, Any]:
    overview = _required_object(chat_report.get("overview"), "chat_report.overview must be an object")
    action_type_counts = _required_object(
        memory_report.get("action_type_counts"),
        "memory_report.action_type_counts must be an object",
    )
    failure_mode_counts = _required_object(
        memory_report.get("failure_mode_counts"),
        "memory_report.failure_mode_counts must be an object",
    )
    checks = {
        "memory_chain_intact": all(
            bool(value)
            for value in _required_object(
                memory_report.get("checks"),
                "memory_report.checks must be an object",
            ).values()
        ),
        "scenario_action_mix_visible": (
            int(action_type_counts.get("look", 0)) >= 1
            and int(action_type_counts.get("notify", 0)) >= 1
            and int(failure_mode_counts.get("network_unavailable", 0)) >= 1
        ),
        "dialogue_thread_reuse_visible": int(overview["dialogue_thread_reuse_cycle_count"]) >= 4,
        "preference_restore_visible": int(overview["preference_restore_cycle_count"]) >= 1,
        "action_transparency_visible": int(overview["response_action_transparency_cycle_count"]) >= 5,
        "failure_explanation_visible": int(overview["response_failure_explanation_cycle_count"]) >= 2,
        "preference_reference_visible": int(overview["response_preference_reference_cycle_count"]) >= 4,
        "controlled_preference_violation": int(overview["response_preference_violation_cycle_count"]) <= 1,
        "mood_tone_hint_visible": int(overview["response_mood_tone_hint_cycle_count"]) >= 4,
    }
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "scenario_name": "memory_write_e2e_chat_behavior_golden",
        "checks": checks,
        "memory_write_e2e": {
            "report_schema_version": int(memory_report["report_schema_version"]),
            "cycle_count": int(memory_report["cycle_count"]),
            "checks": dict(memory_report["checks"]),
            "action_type_counts": dict(action_type_counts),
            "failure_mode_counts": dict(failure_mode_counts),
        },
        "chat_replay_eval": {
            "report_schema_version": int(chat_report["report_schema_version"]),
            "cycle_count": int(chat_report["cycle_count"]),
            "overview": overview,
        },
    }
    if keep_db:
        report["db_path"] = str(db_path)
    return report


# Block: Report validation
def _validate_report(report: dict[str, Any]) -> None:
    checks = _required_object(report.get("checks"), "chat_behavior_golden.checks must be an object")
    if not checks:
        raise RuntimeError("chat_behavior_golden.checks must be non-empty")
    failed_checks = [
        check_name
        for check_name, passed in checks.items()
        if bool(passed) is False
    ]
    if failed_checks:
        raise RuntimeError(
            "chat_behavior_golden failed: " + ", ".join(failed_checks)
        )


# Block: Report formatting
def format_chat_behavior_golden_report(report: dict[str, Any]) -> str:
    checks = _required_object(report.get("checks"), "chat_behavior_golden.checks must be an object")
    memory_report = _required_object(
        report.get("memory_write_e2e"),
        "chat_behavior_golden.memory_write_e2e must be an object",
    )
    chat_report = _required_object(
        report.get("chat_replay_eval"),
        "chat_behavior_golden.chat_replay_eval must be an object",
    )
    overview = _required_object(
        chat_report.get("overview"),
        "chat_behavior_golden.chat_replay_eval.overview must be an object",
    )
    action_type_counts = _required_object(
        memory_report.get("action_type_counts"),
        "chat_behavior_golden.memory_write_e2e.action_type_counts must be an object",
    )
    failure_mode_counts = _required_object(
        memory_report.get("failure_mode_counts"),
        "chat_behavior_golden.memory_write_e2e.failure_mode_counts must be an object",
    )
    lines = [
        "chat behavior golden",
        f"scenario: {report['scenario_name']}",
        (
            "cycles: "
            f"memory_write_e2e {memory_report['cycle_count']}, "
            f"chat_replay_eval {chat_report['cycle_count']}"
        ),
        (
            "action_mix: "
            f"browse {action_type_counts.get('browse', 0)}, "
            f"look {action_type_counts.get('look', 0)}, "
            f"notify {action_type_counts.get('notify', 0)}, "
            f"speak {action_type_counts.get('speak', 0)}, "
            f"network_unavailable {failure_mode_counts.get('network_unavailable', 0)}"
        ),
        (
            "behavior: "
            f"thread_reuse {overview['dialogue_thread_reuse_cycle_count']}, "
            f"preference_reference {overview['response_preference_reference_cycle_count']}, "
            f"preference_violation {overview['response_preference_violation_cycle_count']}, "
            f"action_transparency {overview['response_action_transparency_cycle_count']}, "
            f"failure_explanation {overview['response_failure_explanation_cycle_count']}, "
            f"mood_tone_hint {overview['response_mood_tone_hint_cycle_count']}"
        ),
        "checks: " + ", ".join(
            check_name
            for check_name, passed in checks.items()
            if bool(passed)
        ),
    ]
    db_path = report.get("db_path")
    if isinstance(db_path, str) and db_path:
        lines.append(f"db: {db_path}")
    return "\n".join(lines)


# Block: Required object helper
def _required_object(value: Any, error_message: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError(error_message)
    return value
