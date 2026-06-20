from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlparse

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
class McpServerConfig:
    mcp_server_id: str
    label: str
    command: str
    args: list[str]
    env: dict[str, str]
    cwd: str | None


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    client_id: str
    mcp_servers: list[McpServerConfig]


def load_config(path: Path | None, *, environ: Mapping[str, str] | None = None) -> AppConfig:
    env = environ if environ is not None else os.environ
    raw = _read_json_config(path)
    if "mcp_servers" in raw:
        raise ConfigError("mcp_servers must be configured through OtomeKairo runtime-config API.")
    server = _object(raw.get("server", {}), "server")
    connector = _object(raw.get("connector", {}), "connector")
    base_url = _normalize_base_url(_string_value(server, "base_url", default=_env_value(env, "OTOMEKAIRO_SERVER_URL", "https://127.0.0.1:55601")))
    tls_verify = _bool_value(server, "tls_verify", default=False)
    timeout_seconds = _positive_float(server, "request_timeout_seconds", default=30.0)
    server_config = ServerConfig(
        base_url=base_url,
        access_token=_resolve_access_token(
            server=server,
            environ=env,
            config_path=path,
            base_url=base_url,
            tls_verify=tls_verify,
            request_timeout_seconds=timeout_seconds,
        ),
        tls_verify=tls_verify,
        request_timeout_seconds=timeout_seconds,
        reconnect_delay_seconds=_positive_float(server, "reconnect_delay_seconds", default=5.0),
    )
    client_id = _string_value(connector, "client_id", default="mcp-client-connector-main")
    return AppConfig(
        server=server_config,
        client_id=client_id,
        mcp_servers=_fetch_runtime_mcp_servers(server_config=server_config, client_id=client_id),
    )


def _fetch_runtime_mcp_servers(*, server_config: ServerConfig, client_id: str) -> list[McpServerConfig]:
    client = JsonApiClient(
        base_url=server_config.base_url,
        access_token=server_config.access_token,
        tls_verify=server_config.tls_verify,
        timeout_seconds=server_config.request_timeout_seconds,
    )
    try:
        data = client.get(f"/api/config/connectors/{quote(client_id, safe='')}/runtime-config")
    except HttpError as exc:
        raise ConfigError(f"runtime config fetch failed: {exc}") from exc
    return _mcp_server_configs(data.get("mcp_servers"))


def _mcp_server_configs(value: Any) -> list[McpServerConfig]:
    if not isinstance(value, list) or not value:
        raise ConfigError("mcp_servers must be a non-empty array.")
    servers: list[McpServerConfig] = []
    seen: set[str] = set()
    for item in value:
        server = _object(item, "mcp_servers[]")
        server_id = _required_string(server, "mcp_server_id", "mcp_servers[].mcp_server_id")
        if not server_id.startswith("mcp_server:"):
            raise ConfigError("mcp_servers[].mcp_server_id must start with mcp_server:.")
        if server_id in seen:
            raise ConfigError("mcp_servers contains duplicate mcp_server_id.")
        seen.add(server_id)
        env_values = _string_dict(server.get("env", {}), "mcp_servers[].env")
        servers.append(
            McpServerConfig(
                mcp_server_id=server_id,
                label=_string_value(server, "label", default=server_id),
                command=_required_string(server, "command", "mcp_servers[].command"),
                args=_string_list(server.get("args", []), "mcp_servers[].args"),
                env=env_values,
                cwd=_optional_string(server.get("cwd"), "mcp_servers[].cwd"),
            )
        )
    return servers


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
    explicit_token = _secret_value(server, "access_token", "access_token_env", default_env="OTOMEKAIRO_ACCESS_TOKEN", environ=environ)
    if explicit_token:
        return explicit_token
    local_token = _local_state_access_token(server=server, environ=environ, config_path=config_path)
    if local_token:
        return local_token
    bootstrap_token = _bootstrap_first_console_token(
        base_url=base_url,
        tls_verify=tls_verify,
        request_timeout_seconds=request_timeout_seconds,
    )
    if bootstrap_token:
        return bootstrap_token
    raise ConfigError("access_token could not be resolved from environment, local server state, or bootstrap.")


def _local_state_access_token(*, server: dict[str, Any], environ: Mapping[str, str], config_path: Path | None) -> str:
    for state_path in _candidate_state_paths(server=server, environ=environ, config_path=config_path):
        try:
            with state_path.open("r", encoding="utf-8") as handle:
                state = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            continue
        if isinstance(state, dict) and isinstance(state.get("console_access_token"), str):
            token = state["console_access_token"].strip()
            if token:
                return token
    return ""


def _candidate_state_paths(*, server: dict[str, Any], environ: Mapping[str, str], config_path: Path | None) -> list[Path]:
    paths: list[Path] = []
    for key in ("state_path",):
        value = server.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(Path(value.strip()).expanduser())
    data_dir = server.get("data_dir")
    if isinstance(data_dir, str) and data_dir.strip():
        paths.append(Path(data_dir.strip()).expanduser() / "server_state.json")
    env_data_dir = environ.get("OTOMEKAIRO_DATA_DIR")
    if env_data_dir:
        paths.append(Path(env_data_dir).expanduser() / "server_state.json")
    if config_path is not None:
        paths.append(config_path.resolve().parents[2] / ".local" / "server_state.json")
    return paths


def _bootstrap_first_console_token(*, base_url: str, tls_verify: bool, request_timeout_seconds: float) -> str:
    client = JsonApiClient(base_url=base_url, access_token="", tls_verify=tls_verify, timeout_seconds=request_timeout_seconds)
    try:
        data = client.post("/api/bootstrap/register-first-console", {})
    except HttpError:
        return ""
    token = data.get("console_access_token")
    return token.strip() if isinstance(token, str) else ""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be an object.")
    return value


def _required_string(payload: dict[str, Any], key: str, label: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string.")
    return value.strip()


def _optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string.")
    return value.strip()


def _string_value(payload: dict[str, Any], key: str, *, default: str) -> str:
    value = payload.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string.")
    return value.strip()


def _env_value(environ: Mapping[str, str], key: str, default: str) -> str:
    value = environ.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _secret_value(payload: dict[str, Any], value_key: str, env_key: str, *, default_env: str, environ: Mapping[str, str]) -> str:
    env_name = payload.get(env_key, default_env)
    if isinstance(env_name, str) and env_name.strip():
        env_value = environ.get(env_name.strip())
        if isinstance(env_value, str) and env_value.strip():
            return env_value.strip()
    value = payload.get(value_key)
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _bool_value(payload: dict[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be a boolean.")
    return value


def _positive_float(payload: dict[str, Any], key: str, *, default: float) -> float:
    value = payload.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
        raise ConfigError(f"{key} must be a positive number.")
    return float(value)


def _string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ConfigError(f"{label} must be an array.")
    values: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{label} must contain non-empty strings.")
        values.append(item.strip())
    return values


def _string_dict(value: Any, label: str) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be an object.")
    result: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ConfigError(f"{label} keys must be non-empty strings.")
        if not isinstance(item, str):
            raise ConfigError(f"{label}.{key} must be a string.")
        result[key.strip()] = item
    return result


def _normalize_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError("server.base_url must be an http or https URL.")
    return value.rstrip("/")
