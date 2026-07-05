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
    *,
    environ: Mapping[str, str] | None = None,
) -> AppConfig:
    env = environ if environ is not None else os.environ
    base_url = _normalize_base_url(
        _env_value(env, "OTOMEKAIRO_SERVER_URL", "https://127.0.0.1:55601")
    )
    tls_verify = _env_bool_value(env, "OTOMEKAIRO_TLS_VERIFY", default=False)
    request_timeout_seconds = _env_positive_float(env, "OTOMEKAIRO_WATCHER_REQUEST_TIMEOUT_SECONDS", default=180.0)
    return AppConfig(
        server=ServerConfig(
            base_url=base_url,
            access_token=_resolve_access_token(
                environ=env,
                base_url=base_url,
                tls_verify=tls_verify,
                request_timeout_seconds=request_timeout_seconds,
            ),
            tls_verify=tls_verify,
            request_timeout_seconds=request_timeout_seconds,
            reconnect_delay_seconds=_env_positive_float(
                env,
                "OTOMEKAIRO_WATCHER_RECONNECT_DELAY_SECONDS",
                default=5.0,
            ),
        ),
        watcher=WatcherIdentity(
            watcher_id=_watcher_id(
                _configured_watcher_id(
                    environ=env,
                    default="watcher:tapo_c220_main",
                )
            )
        ),
    )


def _resolve_access_token(
    *,
    environ: Mapping[str, str],
    base_url: str,
    tls_verify: bool,
    request_timeout_seconds: float,
) -> str:
    explicit_token = _env_value(environ, "OTOMEKAIRO_ACCESS_TOKEN", "")
    if explicit_token:
        return explicit_token
    local_token = _local_config_access_token(environ=environ)
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
    environ: Mapping[str, str],
) -> str:
    for db_path in _candidate_config_db_paths(environ=environ):
        if not db_path.is_file():
            continue
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    """
                    SELECT console_access_token
                    FROM server_identity
                    WHERE id = 1
                    """
                ).fetchone()
        except sqlite3.Error:
            continue
        token = row[0] if row is not None else None
        if isinstance(token, str) and token.strip():
            return token.strip()
    return ""


def _candidate_config_db_paths(
    *,
    environ: Mapping[str, str],
) -> list[Path]:
    candidates: list[Path] = []
    config_db_path = _env_value(environ, "OTOMEKAIRO_CONFIG_DB_PATH", "")
    if config_db_path:
        candidates.append(Path(config_db_path).expanduser())
    data_dir = _env_value(environ, "OTOMEKAIRO_DATA_DIR", "")
    if data_dir:
        candidates.append(Path(data_dir).expanduser() / "config.db")
    candidates.append(Path(__file__).resolve().parents[5] / "var" / "otomekairo" / "config.db")
    cwd = Path.cwd()
    candidates.append(cwd / "var" / "otomekairo" / "config.db")
    result: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def _configured_watcher_id(
    *,
    environ: Mapping[str, str],
    default: str,
) -> str:
    explicit = _env_value(environ, "OTOMEKAIRO_WATCHER_ID", "")
    if explicit:
        return explicit

    discovered_ids: list[str] = []
    for db_path in _candidate_config_db_paths(environ=environ):
        discovered_ids.extend(_enabled_watcher_ids_from_config_db(db_path))
    unique_ids = sorted(set(discovered_ids))
    if len(unique_ids) == 1:
        return unique_ids[0]
    if len(unique_ids) > 1:
        raise ConfigError("watcher.watcher_id must be set when multiple enabled watchers exist.")
    return default


def _enabled_watcher_ids_from_config_db(db_path: Path) -> list[str]:
    if not db_path.is_file():
        return []
    try:
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute("SELECT payload_json FROM camera_sources").fetchall()
    except sqlite3.Error:
        return []

    watcher_ids: list[str] = []
    for row in rows:
        try:
            camera_source = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(camera_source, dict) or camera_source.get("enabled") is not True:
            continue
        watcher = camera_source.get("watcher")
        if not isinstance(watcher, dict) or watcher.get("enabled") is not True:
            continue
        watcher_id = watcher.get("watcher_id")
        if isinstance(watcher_id, str) and watcher_id.strip():
            watcher_ids.append(watcher_id.strip())
    return watcher_ids


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


def _env_value(environ: Mapping[str, str], key: str, default: str) -> str:
    value = environ.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _env_bool_value(environ: Mapping[str, str], key: str, *, default: bool) -> bool:
    value = environ.get(key)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{key} must be a boolean.")


def _env_positive_float(environ: Mapping[str, str], key: str, *, default: float) -> float:
    raw_value = environ.get(key)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = float(raw_value.strip())
    except ValueError as exc:
        raise ConfigError(f"{key} must be a positive number.") from exc
    if value <= 0:
        raise ConfigError(f"{key} must be a positive number.")
    return value


def _normalize_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("server.base_url must be an http or https URL.")
    return value.rstrip("/")


def _watcher_id(value: str) -> str:
    if not value.startswith("watcher:"):
        raise ConfigError("watcher.watcher_id must start with watcher:.")
    return value
