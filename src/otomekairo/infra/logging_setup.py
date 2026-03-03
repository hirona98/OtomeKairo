"""Shared logging setup for launcher, web, and runtime processes."""

from __future__ import annotations

import ast
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any


# Block: Constant definitions
SUPPRESSED_ACCESS_PATHS = (
    "\"GET /api/status ",
    "\"GET /api/chat/stream ",
)
_STANDARD_LOG_RECORD_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "taskName",
}


# Block: Empty message filter
class EmptyMessageFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        message = record.getMessage().strip()
        return bool(message)


# Block: Uvicorn access suppression filter
class SuppressFrequentAccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        message = record.getMessage()
        for path in SUPPRESSED_ACCESS_PATHS:
            if path in message:
                return False
        return True


# Block: Console formatter
class ConsoleLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        formatted = (
            f"{self.formatTime(record)} "
            f"{record.levelname} "
            f"{record.name} - "
            f"{_message_summary(message)}"
        )
        pretty_message = _pretty_message_block(message)
        if pretty_message is not None:
            formatted += "\n" + _indented_block(pretty_message)
        if record.exc_info is not None:
            formatted += "\n" + self.formatException(record.exc_info)
        if record.stack_info is not None:
            formatted += "\n" + self.formatStack(record.stack_info)
        extras = _record_extras(record)
        if extras:
            formatted += "\n" + _labeled_json_block("context", extras)
        return formatted

    def formatTime(  # noqa: N802
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:
        del datefmt
        return datetime.fromtimestamp(record.created).strftime("%H:%M:%S")


# Block: File formatter
class FileLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        formatted = (
            f"{self.formatTime(record)} "
            f"{record.levelname} "
            f"{record.name} "
            f"[{record.processName}:{record.process}] "
            f"{record.module}:{record.funcName}:{record.lineno} - "
            f"{_message_summary(message)}"
        )
        pretty_message = _pretty_message_block(message)
        if pretty_message is not None:
            formatted += "\n" + _indented_block(pretty_message)
        if record.exc_info is not None:
            formatted += "\n" + self.formatException(record.exc_info)
        if record.stack_info is not None:
            formatted += "\n" + self.formatStack(record.stack_info)
        extras = _record_extras(record)
        if extras:
            formatted += "\n" + _labeled_json_block("context", extras)
        return formatted

    def formatTime(  # noqa: N802
        self,
        record: logging.LogRecord,
        datefmt: str | None = None,
    ) -> str:
        del datefmt
        return datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")


# Block: Public logging setup
def configure_process_logging(*, process_name: str) -> None:
    if not process_name:
        raise RuntimeError("process_name must be non-empty")
    os.environ["LITELLM_LOG"] = "DEBUG"
    log_dir = _repo_root() / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    _reset_root_handlers(root_logger)
    root_logger.setLevel(logging.DEBUG)

    # Block: Console handler setup
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(
        ConsoleLogFormatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    )

    # Block: File handler setup
    file_handler = logging.FileHandler(
        log_dir / "otomekairo.log",
        mode="a",
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(FileLogFormatter())

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    logging.captureWarnings(True)
    _configure_library_loggers()


# Block: Access logger filter setup
def configure_access_logger_filter() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.setLevel(logging.INFO)
    if not any(
        isinstance(current_filter, SuppressFrequentAccessLogFilter)
        for current_filter in access_logger.filters
    ):
        access_logger.addFilter(SuppressFrequentAccessLogFilter())


# Block: Root handler reset
def _reset_root_handlers(root_logger: logging.Logger) -> None:
    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()


# Block: Library logger configuration
def _configure_library_loggers() -> None:
    for logger_name, level in (
        ("uvicorn", logging.INFO),
        ("uvicorn.error", logging.INFO),
        ("uvicorn.access", logging.INFO),
        ("LiteLLM", logging.DEBUG),
        ("litellm", logging.DEBUG),
        ("py.warnings", logging.WARNING),
        ("asyncio", logging.INFO),
        ("httpcore", logging.WARNING),
        ("httpx", logging.WARNING),
        ("openai", logging.INFO),
    ):
        logging.getLogger(logger_name).setLevel(level)
    _attach_empty_filter("LiteLLM")
    _attach_empty_filter("litellm")
    _attach_empty_filter("py.warnings")


# Block: Filter attach helper
def _attach_filter(logger_name: str, filter_type: type[logging.Filter]) -> None:
    target_logger = logging.getLogger(logger_name)
    if any(isinstance(current_filter, filter_type) for current_filter in target_logger.filters):
        return
    target_logger.addFilter(filter_type())


def _attach_empty_filter(logger_name: str) -> None:
    _attach_filter(logger_name, EmptyMessageFilter)


# Block: Extra extraction
def _record_extras(record: logging.LogRecord) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_LOG_RECORD_KEYS or key.startswith("_"):
            continue
        extras[key] = _json_safe_value(value)
    return extras


# Block: JSON conversion helper
def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    return repr(value)


# Block: Message helpers
def _message_summary(message: str) -> str:
    lines = [line.strip() for line in message.splitlines() if line.strip()]
    if not lines:
        return "(empty)"
    structured_line = _pretty_structured_line(lines[0])
    if structured_line is not None:
        return structured_line[0]
    first_line = " ".join(lines[0].split())
    if len(lines) == 1:
        return first_line
    return f"{first_line} ..."


def _pretty_message_block(message: str) -> str | None:
    lines = [line.rstrip() for line in message.splitlines() if line.strip()]
    if not lines:
        return None
    if len(lines) == 1:
        structured_line = _pretty_structured_line(lines[0])
        if structured_line is None:
            return None
        return structured_line[1]
    formatted_lines: list[str] = []
    for line in lines:
        structured_line = _pretty_structured_line(line)
        if structured_line is not None:
            formatted_lines.extend(structured_line[1].splitlines())
            continue
        formatted_lines.append(line)
    if not formatted_lines:
        return None
    return "\n".join(formatted_lines)


def _pretty_structured_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped:
        return None
    whole_block = _parse_structured_block(stripped)
    if whole_block is not None:
        return ("{...}", whole_block)
    for index, char in enumerate(line):
        if char not in "{[":
            continue
        prefix = line[:index].rstrip()
        if not prefix:
            continue
        suffix = line[index:].strip()
        suffix_block = _parse_structured_block(suffix)
        if suffix_block is None:
            continue
        summary = f"{' '.join(prefix.split())} ..."
        detail_prefix = prefix if prefix.endswith(":") else f"{prefix}:"
        return (summary, f"{detail_prefix}\n{suffix_block}")
    return None


def _parse_structured_block(text: str) -> str | None:
    if not text.startswith(("{", "[")):
        return None
    try:
        parsed_json = json.loads(text)
        return json.dumps(parsed_json, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        pass
    try:
        parsed_literal = ast.literal_eval(text)
    except (SyntaxError, ValueError):
        return None
    if not isinstance(parsed_literal, (dict, list)):
        return None
    normalized_literal = _json_safe_value(parsed_literal)
    return json.dumps(normalized_literal, ensure_ascii=False, indent=2)


def _labeled_json_block(label: str, payload: dict[str, Any]) -> str:
    formatted_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return f"{label}:\n{_indented_block(formatted_json)}"


def _indented_block(text: str) -> str:
    return "\n".join(f"  {line}" for line in text.splitlines())


# Block: Repository root helper
def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]
