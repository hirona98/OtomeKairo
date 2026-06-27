from __future__ import annotations

import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


def _read_non_negative_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(f"{name} must be an integer >= 0.") from exc
    if value < 0:
        raise SystemExit(f"{name} must be an integer >= 0.")
    return value


# 定数
REQUIRED_MODEL_ROLE_NAMES = (
    "expression_generation",
    "decision_generation",
    "autonomous_step_generation",
    "input_interpretation",
    "memory_interpretation",
    "memory_correction_reconciliation",
    "memory_reflection_summary",
    "event_evidence_generation",
    "recall_pack_selection",
    "pending_intent_selection",
)
PENDING_INTENT_NOT_BEFORE_MINUTES = _read_non_negative_int_env(
    "OTOMEKAIRO_PENDING_INTENT_NOT_BEFORE_MINUTES",
    30,
)
PENDING_INTENT_EXPIRES_HOURS = 24
WAKE_RECENT_DEDUPE_WINDOW_MINUTES = 30
BACKGROUND_THINKING_POLL_SECONDS = 5.0
INITIAL_VISUAL_CAPTURE_DELAY_SECONDS = 5.0
DEFAULT_DEBUG_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_DEBUG_LOG_BACKUP_COUNT = 3
MAX_PENDING_DEBUG_STREAM_RECORDS = 200


# デバッグログファイル
_debug_log_file_lock = threading.RLock()
_debug_log_file_path: Path | None = None
_debug_log_file_max_bytes = DEFAULT_DEBUG_LOG_MAX_BYTES
_debug_log_file_backup_count = DEFAULT_DEBUG_LOG_BACKUP_COUNT
_debug_log_stream_sink: Callable[[dict[str, Any]], None] | None = None
_debug_log_stream_pending_records: list[dict[str, Any]] = []


# エラー
class ServiceError(Exception):
    def __init__(self, status_code: int, error_code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message


# デバッグ出力
def configure_debug_log_file(
    log_path: Path | None,
    *,
    max_bytes: int | None = None,
    backup_count: int | None = None,
) -> None:
    global _debug_log_file_backup_count
    global _debug_log_file_max_bytes
    global _debug_log_file_path

    with _debug_log_file_lock:
        _debug_log_file_path = log_path
        _debug_log_file_max_bytes = (
            max_bytes if isinstance(max_bytes, int) and max_bytes > 0 else DEFAULT_DEBUG_LOG_MAX_BYTES
        )
        _debug_log_file_backup_count = (
            backup_count
            if isinstance(backup_count, int) and backup_count >= 0
            else DEFAULT_DEBUG_LOG_BACKUP_COUNT
        )
        if _debug_log_file_path is not None:
            _debug_log_file_path.parent.mkdir(parents=True, exist_ok=True)


def configure_debug_log_stream_sink(sink: Callable[[dict[str, Any]], None] | None) -> None:
    global _debug_log_stream_sink

    with _debug_log_file_lock:
        _debug_log_stream_sink = sink
        pending_records = list(_debug_log_stream_pending_records)
        _debug_log_stream_pending_records.clear()
    if sink is None:
        return
    for record in pending_records:
        try:
            sink(record)
        except Exception as exc:
            print(f"[LogStream] append_failed error={type(exc).__name__}", flush=True)


DEBUG_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
DEBUG_LOG_LEVEL_COLORS = {
    "DEBUG": "\033[36m",
    "INFO": "\033[32m",
    "WARNING": "\033[33m",
    "ERROR": "\033[31m",
}
DEBUG_LOG_COLOR_RESET = "\033[0m"


def debug_log(component: str, message: str, *, level: str = "INFO") -> None:
    timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
    normalized_level = level.strip().upper() if isinstance(level, str) else "INFO"
    if normalized_level not in DEBUG_LOG_LEVELS:
        normalized_level = "INFO"
    line = f"{timestamp} [{normalized_level}] [{component}] {message}"
    print(_terminal_debug_log_line(line, normalized_level), flush=True)
    _append_debug_log_file(line)
    _append_debug_log_stream(
        {
            "ts": timestamp,
            "level": normalized_level,
            "logger": component,
            "msg": message,
        }
    )


def _terminal_debug_log_line(line: str, level: str) -> str:
    if not sys.stdout.isatty():
        return line
    color = DEBUG_LOG_LEVEL_COLORS.get(level)
    if color is None:
        return line
    return f"{color}{line}{DEBUG_LOG_COLOR_RESET}"


def _append_debug_log_stream(record: dict[str, Any]) -> None:
    with _debug_log_file_lock:
        sink = _debug_log_stream_sink
        if sink is None:
            _debug_log_stream_pending_records.append(record)
            if len(_debug_log_stream_pending_records) > MAX_PENDING_DEBUG_STREAM_RECORDS:
                del _debug_log_stream_pending_records[:-MAX_PENDING_DEBUG_STREAM_RECORDS]
            return
    if sink is None:
        return
    try:
        sink(record)
    except Exception as exc:
        print(f"[LogStream] append_failed error={type(exc).__name__}", flush=True)


def _append_debug_log_file(line: str) -> None:
    with _debug_log_file_lock:
        if _debug_log_file_path is None:
            return
        encoded_line = f"{line}\n"
        try:
            _rotate_debug_log_file_if_needed(len(encoded_line.encode("utf-8")))
            with _debug_log_file_path.open("a", encoding="utf-8") as handle:
                handle.write(encoded_line)
        except OSError as exc:
            print(f"{line} [LogFile] write_failed error={type(exc).__name__}", flush=True)


def _rotate_debug_log_file_if_needed(next_write_bytes: int) -> None:
    if _debug_log_file_path is None or _debug_log_file_max_bytes <= 0:
        return
    if not _debug_log_file_path.exists():
        return
    if _debug_log_file_path.stat().st_size + next_write_bytes <= _debug_log_file_max_bytes:
        return
    if _debug_log_file_backup_count <= 0:
        _debug_log_file_path.unlink(missing_ok=True)
        return
    oldest_path = _debug_log_rotated_path(_debug_log_file_backup_count)
    oldest_path.unlink(missing_ok=True)
    for index in range(_debug_log_file_backup_count - 1, 0, -1):
        source_path = _debug_log_rotated_path(index)
        if source_path.exists():
            source_path.replace(_debug_log_rotated_path(index + 1))
    _debug_log_file_path.replace(_debug_log_rotated_path(1))


def _debug_log_rotated_path(index: int) -> Path:
    if _debug_log_file_path is None:
        raise RuntimeError("debug log file path is not configured.")
    return _debug_log_file_path.with_name(f"{_debug_log_file_path.name}.{index}")
