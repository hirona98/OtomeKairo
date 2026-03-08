"""Shared logging setup for launcher, web, and runtime processes."""

from __future__ import annotations

import ast
import base64
import binascii
import json
import logging
import os
import re
from contextlib import contextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from otomekairo.infra.developer_config import DeveloperConfig

# Block: Platform file locking
if os.name == "nt":
    import msvcrt
else:
    import fcntl


# Block: Constant definitions
SUPPRESSED_ACCESS_PATHS = (
    "\"GET /api/status ",
    "\"GET /api/chat/stream ",
)
LOG_FILE_MAX_BYTES = 1024 * 1024
LOG_FILE_BACKUP_COUNT = 5
LOG_LEVEL_NAME_TO_VALUE = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}
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
_SECRET_KEY_NAMES = {
    "api_key",
    "authorization",
    "bot_token",
    "password",
    "token",
}
_SECRET_PATTERNS = (
    re.compile(r"(?i)(api_key\s*=\s*')([^']*)(')"),
    re.compile(r'(?i)("api_key"\s*:\s*")([^"]*)(")'),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([^\s'\"\\]+)"),
)
BASE64_MIN_TEXT_LENGTH = 128
_BASE64_TEXT_PATTERN = re.compile(r"[A-Za-z0-9+/=_-]+")
_BASE64_BLOB_PATTERN = re.compile(
    r"(?<![A-Za-z0-9+/_-])([A-Za-z0-9+/_-]{128,}={0,2})(?![A-Za-z0-9+/_-])"
)
_DATA_URL_BASE64_PATTERN = re.compile(
    r"(data:[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+;base64,)([A-Za-z0-9+/=_-]+)"
)
LITELLM_LOGGER_NAMES = (
    "LiteLLM",
    "LiteLLM Proxy",
    "LiteLLM Router",
    "litellm",
)


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
        message = _sanitize_message(record.getMessage())
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
        message = _sanitize_message(record.getMessage())
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


# Block: Shared rotating file handler
class SharedRotatingFileHandler(RotatingFileHandler):
    def __init__(
        self,
        filename: str | Path,
        *,
        max_bytes: int,
        backup_count: int,
        encoding: str,
    ) -> None:
        super().__init__(
            filename=filename,
            mode="a",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding=encoding,
            delay=True,
        )
        self._lock_stream = Path(f"{self.baseFilename}.lock").open("a+b")
        if self._lock_stream.tell() == 0:
            self._lock_stream.write(b"0")
            self._lock_stream.flush()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with _interprocess_lock(self._lock_stream):
                try:
                    if self.stream is None:
                        self.stream = self._open()
                    if self.shouldRollover(record):
                        self.doRollover()
                    logging.FileHandler.emit(self, record)
                finally:
                    self._close_stream_after_emit()
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        try:
            self._close_stream_after_emit()
        finally:
            if not self._lock_stream.closed:
                self._lock_stream.close()
            super().close()

    def _close_stream_after_emit(self) -> None:
        if self.stream is None:
            return
        try:
            self.stream.flush()
        finally:
            self.stream.close()
            self.stream = None


# Block: Public logging setup
def configure_process_logging(*, process_name: str, developer_config: DeveloperConfig) -> None:
    if not process_name:
        raise RuntimeError("process_name must be non-empty")
    process_logging = _process_logging_config(
        process_name=process_name,
        developer_config=developer_config,
    )
    os.environ["LITELLM_LOG"] = developer_config.litellm.log_level
    log_dir = _repo_root() / "log"
    log_dir.mkdir(parents=True, exist_ok=True)
    root_logger = logging.getLogger()
    _reset_root_handlers(root_logger)
    root_logger.setLevel(_log_level_value(process_logging.root_level))

    # Block: Console handler setup
    console_handler = logging.StreamHandler()
    console_handler.setLevel(_log_level_value(process_logging.console_level))
    console_handler.setFormatter(
        ConsoleLogFormatter("%(asctime)s %(levelname)s %(name)s - %(message)s")
    )

    # Block: File handler setup
    file_handler = SharedRotatingFileHandler(
        log_dir / "otomekairo.log",
        max_bytes=LOG_FILE_MAX_BYTES,
        backup_count=LOG_FILE_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(_log_level_value(process_logging.file_level))
    file_handler.setFormatter(FileLogFormatter())

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    logging.captureWarnings(True)
    _configure_library_loggers(
        logger_levels=process_logging.logger_levels,
        litellm_log_level=developer_config.litellm.log_level,
    )


# Block: Access logger filter setup
def configure_access_logger_filter() -> None:
    access_logger = logging.getLogger("uvicorn.access")
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
def _configure_library_loggers(*, logger_levels: dict[str, str], litellm_log_level: str) -> None:
    for logger_name, level_name in logger_levels.items():
        logging.getLogger(logger_name).setLevel(_log_level_value(level_name))
    configure_litellm_logger_bridge(litellm_log_level=litellm_log_level)
    _attach_empty_filter("py.warnings")


# Block: Filter attach helper
def _attach_filter(logger_name: str, filter_type: type[logging.Filter]) -> None:
    target_logger = logging.getLogger(logger_name)
    if any(isinstance(current_filter, filter_type) for current_filter in target_logger.filters):
        return
    target_logger.addFilter(filter_type())


def _attach_empty_filter(logger_name: str) -> None:
    _attach_filter(logger_name, EmptyMessageFilter)


# Block: LiteLLM logger bridge
def configure_litellm_logger_bridge(*, litellm_log_level: str) -> None:
    for logger_name in LITELLM_LOGGER_NAMES:
        target_logger = logging.getLogger(logger_name)
        _reset_named_logger_handlers(target_logger)
        target_logger.disabled = False
        target_logger.propagate = True
        target_logger.setLevel(_log_level_value(litellm_log_level))
        _attach_empty_filter(logger_name)


# Block: Interprocess lock helper
@contextmanager
def _interprocess_lock(lock_stream: Any):
    if os.name == "nt":
        lock_stream.seek(0)
        msvcrt.locking(lock_stream.fileno(), msvcrt.LK_LOCK, 1)
        try:
            yield
        finally:
            lock_stream.seek(0)
            msvcrt.locking(lock_stream.fileno(), msvcrt.LK_UNLCK, 1)
        return

    fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
    try:
        yield
    finally:
        fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)


# Block: Process config helpers
def _process_logging_config(*, process_name: str, developer_config: DeveloperConfig):
    process_logging = developer_config.process_logging.get(process_name)
    if process_logging is None:
        raise RuntimeError(f"developer_config.process.{process_name} is missing")
    return process_logging


# Block: Named logger handler reset
def _reset_named_logger_handlers(target_logger: logging.Logger) -> None:
    for handler in list(target_logger.handlers):
        target_logger.removeHandler(handler)
        handler.close()


def _log_level_value(level_name: str) -> int:
    level_value = LOG_LEVEL_NAME_TO_VALUE.get(level_name)
    if level_value is None:
        raise RuntimeError(f"unsupported log level: {level_name}")
    return level_value


# Block: Extra extraction
def _record_extras(record: logging.LogRecord) -> dict[str, Any]:
    extras: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_LOG_RECORD_KEYS or key.startswith("_"):
            continue
        extras[key] = _json_safe_value(value, parent_key=key)
    return extras


# Block: JSON conversion helper
def _json_safe_value(value: Any, *, parent_key: str | None = None) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str):
            return _sanitize_scalar(parent_key=parent_key, value=value)
        return value
    if isinstance(value, dict):
        return {
            str(key): _json_safe_value(item, parent_key=str(key))
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item, parent_key=parent_key) for item in value]
    return repr(value)


# Block: Secret sanitizers
def _sanitize_message(message: str) -> str:
    sanitized = _omit_base64_text(message)
    for pattern in _SECRET_PATTERNS:
        sanitized = pattern.sub(_mask_secret_match, sanitized)
    return sanitized


def _mask_secret_match(match: re.Match[str]) -> str:
    suffix = match.group(3) if match.lastindex is not None and match.lastindex >= 3 else ""
    return f"{match.group(1)}{_mask_secret_text(match.group(2))}{suffix}"


def _sanitize_scalar(*, parent_key: str | None, value: str) -> str:
    if parent_key is None:
        return _sanitize_message(value)
    normalized_key = parent_key.lower()
    if normalized_key in _SECRET_KEY_NAMES:
        return _mask_secret_text(value)
    return _sanitize_message(value)


def _mask_secret_text(value: str) -> str:
    if not value:
        return value
    if len(value) <= 4:
        return "*" * len(value)
    return f"{value[:2]}***{value[-2:]}"


# Block: Base64 sanitizers
def _omit_base64_text(text: str) -> str:
    sanitized = _DATA_URL_BASE64_PATTERN.sub(_replace_data_url_base64_match, text)
    return _BASE64_BLOB_PATTERN.sub(_replace_base64_blob_match, sanitized)


def _replace_data_url_base64_match(match: re.Match[str]) -> str:
    payload = match.group(2)
    if not _looks_like_base64_blob(payload):
        return match.group(0)
    return f"{match.group(1)}{_omitted_base64_marker(payload)}"


def _replace_base64_blob_match(match: re.Match[str]) -> str:
    payload = match.group(1)
    if not _looks_like_base64_blob(payload):
        return payload
    return _omitted_base64_marker(payload)


def _looks_like_base64_blob(text: str) -> bool:
    if len(text) < BASE64_MIN_TEXT_LENGTH:
        return False
    if _BASE64_TEXT_PATTERN.fullmatch(text) is None:
        return False
    padding_index = text.find("=")
    if padding_index != -1 and text[padding_index:] != "=" * (len(text) - padding_index):
        return False
    normalized = text.replace("-", "+").replace("_", "/")
    remainder = len(normalized) % 4
    if remainder == 1:
        return False
    if remainder != 0:
        normalized += "=" * (4 - remainder)
    try:
        decoded = base64.b64decode(normalized, validate=True)
    except (binascii.Error, ValueError):
        return False
    return len(decoded) > 0


def _omitted_base64_marker(text: str) -> str:
    return f"[BASE64 omitted length={len(text)}]"


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
