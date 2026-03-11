"""Unified deterministic evaluation gate for merge-time verification."""

from __future__ import annotations

import py_compile
from pathlib import Path
from typing import Any

from otomekairo.usecase.chat_behavior_golden import build_chat_behavior_golden_report


# Block: Report constants
REPORT_SCHEMA_VERSION = 1


# Block: Public gate runner
def run_eval_gate(*, keep_db: bool) -> dict[str, Any]:
    source_root = Path(__file__).resolve().parents[1]
    py_compile_report = _run_py_compile_gate(source_root=source_root)
    chat_behavior_report = build_chat_behavior_golden_report(
        keep_db=keep_db,
    )
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "checks": {
            "py_compile_ok": True,
            "chat_behavior_golden_ok": True,
        },
        "py_compile": py_compile_report,
        "chat_behavior_golden": chat_behavior_report,
    }
    _validate_report(report)
    return report


# Block: Py compile gate
def _run_py_compile_gate(*, source_root: Path) -> dict[str, Any]:
    python_files = sorted(
        path
        for path in source_root.rglob("*.py")
        if "__pycache__" not in path.parts
    )
    if not python_files:
        raise RuntimeError("eval_gate found no python files under src/otomekairo")
    compiled_paths: list[str] = []
    for python_file in python_files:
        try:
            py_compile.compile(str(python_file), doraise=True)
        except py_compile.PyCompileError as exc:
            raise RuntimeError(f"eval_gate py_compile failed: {python_file}: {exc.msg}") from exc
        compiled_paths.append(str(python_file.relative_to(source_root.parent)))
    return {
        "source_root": str(source_root),
        "checked_file_count": len(compiled_paths),
        "checked_files": compiled_paths,
    }


# Block: Report validation
def _validate_report(report: dict[str, Any]) -> None:
    checks = report.get("checks")
    if not isinstance(checks, dict):
        raise RuntimeError("eval_gate.checks must be an object")
    failed_checks = [
        check_name
        for check_name, passed in checks.items()
        if bool(passed) is False
    ]
    if failed_checks:
        raise RuntimeError("eval_gate failed: " + ", ".join(failed_checks))


# Block: Report formatter
def format_eval_gate_report(report: dict[str, Any]) -> str:
    checks = report.get("checks")
    if not isinstance(checks, dict):
        raise RuntimeError("eval_gate.checks must be an object")
    py_compile_report = report.get("py_compile")
    if not isinstance(py_compile_report, dict):
        raise RuntimeError("eval_gate.py_compile must be an object")
    chat_behavior_report = report.get("chat_behavior_golden")
    if not isinstance(chat_behavior_report, dict):
        raise RuntimeError("eval_gate.chat_behavior_golden must be an object")
    golden_checks = chat_behavior_report.get("checks")
    if not isinstance(golden_checks, dict):
        raise RuntimeError("eval_gate.chat_behavior_golden.checks must be an object")
    lines = [
        "eval gate",
        f"py_compile: {py_compile_report['checked_file_count']} files",
        "checks: " + ", ".join(
            check_name
            for check_name, passed in checks.items()
            if bool(passed)
        ),
        "golden: " + ", ".join(
            check_name
            for check_name, passed in golden_checks.items()
            if bool(passed)
        ),
    ]
    db_path = chat_behavior_report.get("db_path")
    if isinstance(db_path, str) and db_path:
        lines.append(f"db: {db_path}")
    return "\n".join(lines)
