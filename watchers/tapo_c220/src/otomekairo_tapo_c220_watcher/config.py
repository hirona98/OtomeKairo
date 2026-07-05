from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .http import HttpError, JsonApiClient


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ServerConfig:
    base_url: str
    access_token: str
    tls_verify: bool
    request_timeout_seconds: float
    reconnect_delay_seconds: float


@dataclass(frozen=True)
class WatcherIdentity:
    watcher_id: str


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    watcher: WatcherIdentity


def load_config(
    path: Path | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> AppConfig:
    env = environ if environ is not None else os.environ
    raw = _read_json_config(path)
    server = _object(raw.get("server", {}), "server")
    watcher = _object(raw.get("watcher", {}), "watcher")
    base_url = _normalize_base_url(
        _string_value(
            server,
            "base_url",
            default=_env_value(env, "OTOMEKAIRO_SERVER_URL", "https://127.0.0.1:55601"),
        )
    )
    tls_verify = _bool_value(server, "tls_verify", default=False)
    request_timeout_seconds = _positive_float(server, "request_timeout_seconds", default=180.0)
    return AppConfig(
        server=ServerConfig(
            base_url=base_url,
            access_token=_resolve_access_token(
                server=server,
                environ=env,
                config_path=path,
                base_url=base_url,
                tls_verify=tls_verify,
                request_timeout_seconds=request_timeout_seconds,
            ),
            tls_verify=tls_verify,
            request_timeout_seconds=request_timeout_seconds,
            reconnect_delay_seconds=_positive_float(server, "reconnect_delay_seconds", default=5.0),
        ),
        watcher=WatcherIdentity(
            watcher_id=_watcher_id(_string_value(watcher, "watcher_id", default="watcher:tapo_c220_main"))
        ),
    )


def _read_json_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"config file was not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ConfigError("config root must be an object.")
    return payload


def _resolve_access_token(
    *,
    server: dict[str, Any],
    environ: Mapping[str, str],
    config_path: Path | None,
    base_url: str,
    tls_verify: bool,
    request_timeout_seconds: float,
) -> str:
    explicit_token = _secret_value(
        server,
        "access_token",
        "access_token_env",
        default_env="OTOMEKAIRO_ACCESS_TOKEN",
        environ=environ,
        required=False,
    )
    if explicit_token:
        return explicit_token
    local_token = _local_config_access_token(server=server, environ=environ, config_path=config_path)
    if local_token:
        return local_token
    bootstrap_token = _bootstrap_first_console_token(
        base_url=base_url,
        tls_verify=tls_verify,
        request_timeout_seconds=request_timeout_seconds,
    )
    if bootstrap_token:
        return bootstrap_token
    raise ConfigError("access_token could not be resolved from environment, local config.db, or bootstrap.")


def _local_config_access_token(
    *,
    server: dict[str, Any],
    environ: Mapping[str, str],
    config_path: Path | None,
) -> str:
    for db_path in _candidate_config_db_paths(server=server, environ=environ, config_path=config_path):
        if not db_path.is_file():
            continue
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute("SELECT state_json FROM state WHERE state_key = 'server'").fetchone()
        except sqlite3.Error:
            continue
        if not row:
            continue
        try:
            state = json.loads(row[0])
        except json.JSONDecodeError:
            continue
        token = state.get("console_access_token") if isinstance(state, dict) else None
        if isinstance(token, str) and token.strip():
            return token.strip()
    return ""


def _candidate_config_db_paths(
    *,
    server: dict[str, Any],
    environ: Mapping[str, str],
    config_path: Path | None,
) -> list[Path]:
    candidates: list[Path] = []
    for value in (
        _string_value(server, "config_db_path", default=""),
        _env_value(environ, "OTOMEKAIRO_CONFIG_DB_PATH", ""),
    ):
        if value:
            candidates.append(Path(value).expanduser())
    for value in (
        _string_value(server, "data_dir", default=""),
        _env_value(environ, "OTOMEKAIRO_DATA_DIR", ""),
    ):
        if value:
            candidates.append(Path(value).expanduser() / "config.db")
    if config_path is not None and len(config_path.resolve().parents) >= 3:
        candidates.append(config_path.resolve().parents[2] / "var" / "otomekairo" / "config.db")
    cwd = Path.cwd()
    if len(cwd.parents) >= 2:
        candidates.append(cwd.parents[1] / "var" / "otomekairo" / "config.db")
    result: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def _bootstrap_first_console_token(
    *,
    base_url: str,
    tls_verify: bool,
    request_timeout_seconds: float,
) -> str:
    client = JsonApiClient(
        base_url=base_url,
        access_token="",
        tls_verify=tls_verify,
        timeout_seconds=request_timeout_seconds,
    )
    try:
        probe = client.get("/api/bootstrap/probe")
        if probe.get("bootstrap_state") != "unregistered":
            return ""
        data = client.post("/api/bootstrap/register-first-console", {})
    except HttpError:
        return ""
    token = data.get("console_access_token")
    return token.strip() if isinstance(token, str) and token.strip() else ""


def _secret_value(
    definition: dict[str, Any],
    key: str,
    env_key: str,
    *,
    default_env: str,
    environ: Mapping[str, str],
    required: bool,
) -> str:
    env_name = definition.get(env_key)
    if isinstance(env_name, str) and env_name.strip():
        value = environ.get(env_name.strip(), "")
        if value.strip():
            return value.strip()
    value = definition.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    default_value = environ.get(default_env, "")
    if default_value.strip():
        return default_value.strip()
    if required:
        raise ConfigError(f"{key} must be set directly or through {env_key}.")
    return ""


def _object(value: Any, label: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be an object.")
    return value


def _string_value(definition: dict[str, Any], key: str, *, default: str) -> str:
    value = definition.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str):
        raise ConfigError(f"{key} must be a string.")
    return value.strip() or default


def _env_value(environ: Mapping[str, str], key: str, default: str) -> str:
    value = environ.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _bool_value(definition: dict[str, Any], key: str, *, default: bool) -> bool:
    value = definition.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be a boolean.")
    return value


def _positive_float(definition: dict[str, Any], key: str, *, default: float) -> float:
    value = definition.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or float(value) <= 0:
        raise ConfigError(f"{key} must be a positive number.")
    return float(value)


def _normalize_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("server.base_url must be an http or https URL.")
    return value.rstrip("/")


def _watcher_id(value: str) -> str:
    if not value.startswith("watcher:"):
        raise ConfigError("watcher.watcher_id must start with watcher:.")
    return value
