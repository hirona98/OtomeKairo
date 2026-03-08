"""Developer-only startup configuration."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# Block: Schema constants
DEVELOPER_CONFIG_SCHEMA_VERSION = 1
SUPPORTED_PROCESS_NAMES = ("launcher", "web", "runtime")
FORBIDDEN_PROCESS_LOGGER_NAMES = frozenset({"LiteLLM", "litellm"})
SUPPORTED_LOG_LEVEL_NAMES = frozenset(
    {
        "CRITICAL",
        "ERROR",
        "WARNING",
        "INFO",
        "DEBUG",
        "NOTSET",
    }
)


# Block: Data models
@dataclass(frozen=True, slots=True)
class ProcessLoggingConfig:
    root_level: str
    console_level: str
    file_level: str
    logger_levels: dict[str, str]


@dataclass(frozen=True, slots=True)
class LiteLLMDeveloperConfig:
    log_level: str


@dataclass(frozen=True, slots=True)
class DeveloperConfig:
    schema_version: int
    process_logging: dict[str, ProcessLoggingConfig]
    litellm: LiteLLMDeveloperConfig


# Block: Public loader
def load_developer_config(repo_root: Path | None = None) -> DeveloperConfig:
    config_path = _developer_config_path(repo_root)
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise RuntimeError(f"developer config file is missing: {config_path}") from error
    try:
        raw_config = tomllib.loads(raw_text)
    except tomllib.TOMLDecodeError as error:
        raise RuntimeError(f"failed to parse {config_path}: {error}") from error
    return _validate_developer_config(raw_config=raw_config)


# Block: Path helper
def _developer_config_path(repo_root: Path | None) -> Path:
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "config" / "developer.toml"


# Block: Top-level validation
def _validate_developer_config(*, raw_config: Any) -> DeveloperConfig:
    config_dict = _require_dict(raw_config, "developer_config")
    _require_exact_keys(
        config_dict,
        "developer_config",
        ("schema_version", "process", "integrations"),
    )
    schema_version = _require_int(config_dict.get("schema_version"), "developer_config.schema_version")
    if schema_version != DEVELOPER_CONFIG_SCHEMA_VERSION:
        raise RuntimeError(
            "developer_config.schema_version must be 1"
        )
    process_logging = _validate_process_section(config_dict.get("process"))
    litellm = _validate_integrations_section(config_dict.get("integrations"))
    return DeveloperConfig(
        schema_version=schema_version,
        process_logging=process_logging,
        litellm=litellm,
    )


# Block: Process section validation
def _validate_process_section(raw_process: Any) -> dict[str, ProcessLoggingConfig]:
    process_dict = _require_dict(raw_process, "developer_config.process")
    _require_exact_keys(process_dict, "developer_config.process", SUPPORTED_PROCESS_NAMES)
    return {
        process_name: _validate_process_entry(
            process_name=process_name,
            raw_process_entry=process_dict.get(process_name),
        )
        for process_name in SUPPORTED_PROCESS_NAMES
    }


# Block: Single process validation
def _validate_process_entry(*, process_name: str, raw_process_entry: Any) -> ProcessLoggingConfig:
    process_dict = _require_dict(raw_process_entry, f"developer_config.process.{process_name}")
    _require_exact_keys(process_dict, f"developer_config.process.{process_name}", ("logging",))
    return _validate_logging_config(
        raw_logging=process_dict.get("logging"),
        field_name=f"developer_config.process.{process_name}.logging",
    )


# Block: Logging validation
def _validate_logging_config(*, raw_logging: Any, field_name: str) -> ProcessLoggingConfig:
    logging_dict = _require_dict(raw_logging, field_name)
    _require_exact_keys(
        logging_dict,
        field_name,
        ("root_level", "console_level", "file_level", "loggers"),
    )
    root_level = _require_log_level(logging_dict.get("root_level"), f"{field_name}.root_level")
    console_level = _require_log_level(logging_dict.get("console_level"), f"{field_name}.console_level")
    file_level = _require_log_level(logging_dict.get("file_level"), f"{field_name}.file_level")
    logger_levels = _validate_logger_levels(logging_dict.get("loggers"), f"{field_name}.loggers")
    return ProcessLoggingConfig(
        root_level=root_level,
        console_level=console_level,
        file_level=file_level,
        logger_levels=logger_levels,
    )


# Block: Logger mapping validation
def _validate_logger_levels(raw_loggers: Any, field_name: str) -> dict[str, str]:
    logger_dict = _require_dict(raw_loggers, field_name)
    normalized: dict[str, str] = {}
    for logger_name, raw_level in logger_dict.items():
        if not isinstance(logger_name, str) or not logger_name:
            raise RuntimeError(f"{field_name} keys must be non-empty strings")
        if logger_name in FORBIDDEN_PROCESS_LOGGER_NAMES:
            raise RuntimeError(
                f"{field_name}.{logger_name} is not allowed; use developer_config.integrations.litellm.log_level"
            )
        normalized[logger_name] = _require_log_level(raw_level, f"{field_name}.{logger_name}")
    return normalized


# Block: Integration validation
def _validate_integrations_section(raw_integrations: Any) -> LiteLLMDeveloperConfig:
    integrations_dict = _require_dict(raw_integrations, "developer_config.integrations")
    _require_exact_keys(integrations_dict, "developer_config.integrations", ("litellm",))
    litellm_dict = _require_dict(
        integrations_dict.get("litellm"),
        "developer_config.integrations.litellm",
    )
    _require_exact_keys(
        litellm_dict,
        "developer_config.integrations.litellm",
        ("log_level",),
    )
    return LiteLLMDeveloperConfig(
        log_level=_require_log_level(
            litellm_dict.get("log_level"),
            "developer_config.integrations.litellm.log_level",
        )
    )


# Block: Primitive validators
def _require_dict(raw_value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(raw_value, dict):
        raise RuntimeError(f"{field_name} must be table")
    return raw_value


def _require_exact_keys(raw_dict: dict[str, Any], field_name: str, expected_keys: tuple[str, ...] | frozenset[str]) -> None:
    actual_keys = set(raw_dict.keys())
    expected_key_set = set(expected_keys)
    if actual_keys != expected_key_set:
        missing_keys = sorted(expected_key_set - actual_keys)
        extra_keys = sorted(actual_keys - expected_key_set)
        details: list[str] = []
        if missing_keys:
            details.append(f"missing={','.join(missing_keys)}")
        if extra_keys:
            details.append(f"extra={','.join(extra_keys)}")
        raise RuntimeError(f"{field_name} keys are invalid ({' '.join(details)})")


def _require_int(raw_value: Any, field_name: str) -> int:
    if not isinstance(raw_value, int) or isinstance(raw_value, bool):
        raise RuntimeError(f"{field_name} must be integer")
    return raw_value


def _require_log_level(raw_value: Any, field_name: str) -> str:
    if not isinstance(raw_value, str) or not raw_value:
        raise RuntimeError(f"{field_name} must be non-empty string")
    normalized = raw_value.upper()
    if normalized not in SUPPORTED_LOG_LEVEL_NAMES:
        raise RuntimeError(
            f"{field_name} must be one of {','.join(sorted(SUPPORTED_LOG_LEVEL_NAMES))}"
        )
    return normalized
