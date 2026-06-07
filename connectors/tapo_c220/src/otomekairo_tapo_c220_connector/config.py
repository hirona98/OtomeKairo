from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote, urlparse

from .http import HttpError, JsonApiClient


SUPPORTED_OPERATIONS = ("move_up", "move_down", "move_left", "move_right")
SUPPORTED_AMOUNTS = ("small", "medium")
# C220 ContinuousMove の符号を、現在映像に対する相対方向へ合わせる。
DEFAULT_OPERATION_VECTORS: dict[str, tuple[float, float]] = {
    "move_up": (0, 1),
    "move_down": (0, -1),
    "move_left": (1, 0),
    "move_right": (-1, 0),
}


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
class ConnectorConfig:
    client_id: str
    vision_source_id: str
    label: str
    aliases: list[str]
    default_for: list[str]


@dataclass(frozen=True)
class CameraConfig:
    host: str
    camera_username: str
    camera_password: str
    onvif_port: int
    rtsp_port: int
    rtsp_path: str
    rtsp_transport: str
    rtsp_open_timeout_seconds: float
    jpeg_quality: int
    small_move_seconds: float
    medium_move_seconds: float
    operation_vectors: dict[str, tuple[float, float]]


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    connector: ConnectorConfig
    camera: CameraConfig

    def hello_payload(self) -> dict[str, Any]:
        return {
            "type": "hello",
            "client_id": self.connector.client_id,
            "caps": [
                {"id": "vision.capture", "version": "1"},
                {"id": "camera.ptz", "version": "1"},
            ],
            "vision_sources": [
                {
                    "vision_source_id": self.connector.vision_source_id,
                    "capability_id": "vision.capture",
                    "kind": "camera",
                    "source_owner": "self",
                    "label": self.connector.label,
                    "aliases": self.connector.aliases,
                    "default_for": self.connector.default_for,
                    "required_permissions": ["observe_vision", "observe_camera"],
                    "supported_controls": {
                        "camera.ptz": {
                            "operations": list(SUPPORTED_OPERATIONS),
                            "amounts": list(SUPPORTED_AMOUNTS),
                        }
                    },
                }
            ],
        }


def load_config(
    path: Path | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> AppConfig:
    env = environ if environ is not None else os.environ
    raw = _read_json_config(path)
    server = _object(raw.get("server", {}), "server")
    connector = _object(raw.get("connector", {}), "connector")

    server_config = ServerConfig(
        base_url=_normalize_base_url(
            _string_value(server, "base_url", default=_env_value(env, "OTOMEKAIRO_SERVER_URL", "https://127.0.0.1:55601"))
        ),
        access_token=_secret_value(
            server,
            "access_token",
            "access_token_env",
            default_env="OTOMEKAIRO_ACCESS_TOKEN",
            environ=env,
            required=True,
        ),
        tls_verify=_bool_value(server, "tls_verify", default=False),
        request_timeout_seconds=_positive_float(server, "request_timeout_seconds", default=10.0),
        reconnect_delay_seconds=_positive_float(server, "reconnect_delay_seconds", default=5.0),
    )

    client_id = _string_value(connector, "client_id", default="tapo-c220-connector-main")
    runtime_source = _fetch_runtime_camera_source(server_config=server_config, client_id=client_id)
    if runtime_source.get("enabled") is not True:
        raise ConfigError("runtime camera_source.enabled must be true.")
    label = _required_string(runtime_source, "label", "runtime camera_source.label")
    connector_config = ConnectorConfig(
        client_id=client_id,
        vision_source_id=_vision_source_id(
            _required_string(runtime_source, "vision_source_id", "runtime camera_source.vision_source_id")
        ),
        label=label,
        aliases=[label],
        default_for=["camera"],
    )

    connection = _object(runtime_source.get("connection"), "runtime camera_source.connection")
    camera_config = CameraConfig(
        host=_required_string(connection, "host", "runtime camera_source.connection.host"),
        camera_username=_required_string(
            connection,
            "camera_username",
            "runtime camera_source.connection.camera_username",
        ),
        camera_password=_required_string(
            connection,
            "camera_password",
            "runtime camera_source.connection.camera_password",
        ),
        onvif_port=2020,
        rtsp_port=554,
        rtsp_path="stream1",
        rtsp_transport="tcp",
        rtsp_open_timeout_seconds=8.0,
        jpeg_quality=88,
        small_move_seconds=0.20,
        medium_move_seconds=0.55,
        operation_vectors=dict(DEFAULT_OPERATION_VECTORS),
    )

    return AppConfig(server=server_config, connector=connector_config, camera=camera_config)


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


def _fetch_runtime_camera_source(*, server_config: ServerConfig, client_id: str) -> dict[str, Any]:
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
    sources = data.get("camera_sources")
    if not isinstance(sources, list):
        raise ConfigError("runtime config camera_sources must be an array.")
    tapo_sources = [
        source
        for source in sources
        if isinstance(source, dict) and source.get("connector_kind") == "tapo_c220"
    ]
    if len(tapo_sources) != 1:
        raise ConfigError("runtime config must contain exactly one tapo_c220 camera_source for this client_id.")
    return tapo_sources[0]


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{label} must be an object.")
    return value


def _env_value(environ: Mapping[str, str], name: str, default: str) -> str:
    value = environ.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _string_value(section: dict[str, Any], key: str, *, default: str) -> str:
    value = section.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{key} must be a non-empty string.")
    return value.strip()


def _required_string(section: dict[str, Any], key: str, label: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string.")
    return value.strip()


def _secret_value(
    section: dict[str, Any],
    value_key: str,
    env_key: str,
    *,
    default_env: str,
    environ: Mapping[str, str],
    required: bool,
) -> str:
    env_name = section.get(env_key, default_env)
    if env_name is not None:
        if not isinstance(env_name, str) or not env_name.strip():
            raise ConfigError(f"{env_key} must be a non-empty string.")
        env_value = environ.get(env_name.strip())
        if isinstance(env_value, str) and env_value.strip():
            return env_value.strip()
    value = section.get(value_key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if required:
        raise ConfigError(f"{value_key} is required through {env_key} or local config.")
    return ""


def _bool_value(section: dict[str, Any], key: str, *, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be a boolean.")
    return value


def _positive_float(section: dict[str, Any], key: str, *, default: float) -> float:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ConfigError(f"{key} must be a positive number.")
    return float(value)


def _vision_source_id(value: str) -> str:
    if not value.startswith("vision_source:"):
        raise ConfigError("vision_source_id must start with vision_source:.")
    return value


def _normalize_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        raise ConfigError("server.base_url must use http or https.")
    if not parsed.hostname:
        raise ConfigError("server.base_url must include host.")
    return value.rstrip("/")
