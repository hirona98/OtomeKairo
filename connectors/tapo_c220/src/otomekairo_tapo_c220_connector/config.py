from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


SUPPORTED_OPERATIONS = ("move_up", "move_down", "move_left", "move_right")
SUPPORTED_AMOUNTS = ("small", "medium")
DEFAULT_OPERATION_VECTORS: dict[str, tuple[float, float]] = {
    "move_up": (0, -1),
    "move_down": (0, 1),
    "move_left": (-1, 0),
    "move_right": (1, 0),
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
    require_runtime_secrets: bool = True,
) -> AppConfig:
    env = environ if environ is not None else os.environ
    raw = _read_json_config(path)
    server = _object(raw.get("server", {}), "server")
    connector = _object(raw.get("connector", {}), "connector")
    camera = _object(raw.get("camera", {}), "camera")

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
            required=require_runtime_secrets,
        ),
        tls_verify=_bool_value(server, "tls_verify", default=False),
        request_timeout_seconds=_positive_float(server, "request_timeout_seconds", default=10.0),
        reconnect_delay_seconds=_positive_float(server, "reconnect_delay_seconds", default=5.0),
    )

    connector_config = ConnectorConfig(
        client_id=_string_value(connector, "client_id", default="tapo-c220-connector-main"),
        vision_source_id=_vision_source_id(
            _string_value(connector, "vision_source_id", default="vision_source:tapo_c220_main")
        ),
        label=_string_value(connector, "label", default="C220"),
        aliases=_string_list(connector, "aliases", default=["camera", "C220"]),
        default_for=_string_list(connector, "default_for", default=["visual", "camera"]),
    )

    camera_username = _secret_value(
        camera,
        "camera_username",
        "camera_username_env",
        default_env="TAPO_C220_CAMERA_USERNAME",
        environ=env,
        required=require_runtime_secrets,
    )
    camera_password = _secret_value(
        camera,
        "camera_password",
        "camera_password_env",
        default_env="TAPO_C220_CAMERA_PASSWORD",
        environ=env,
        required=require_runtime_secrets,
    )
    camera_config = CameraConfig(
        host=_secret_value(
            camera,
            "host",
            "host_env",
            default_env="TAPO_C220_HOST",
            environ=env,
            required=require_runtime_secrets,
        ),
        camera_username=camera_username,
        camera_password=camera_password,
        onvif_port=_port_value(camera, "onvif_port", default=2020),
        rtsp_port=_port_value(camera, "rtsp_port", default=554),
        rtsp_path=_rtsp_path(_string_value(camera, "rtsp_path", default="stream1")),
        rtsp_transport=_rtsp_transport(_string_value(camera, "rtsp_transport", default="tcp")),
        rtsp_open_timeout_seconds=_positive_float(camera, "rtsp_open_timeout_seconds", default=8.0),
        jpeg_quality=_int_range(camera, "jpeg_quality", default=88, minimum=1, maximum=95),
        small_move_seconds=_positive_float(camera, "small_move_seconds", default=0.20),
        medium_move_seconds=_positive_float(camera, "medium_move_seconds", default=0.55),
        operation_vectors=_operation_vectors(camera.get("operation_vectors", DEFAULT_OPERATION_VECTORS)),
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


def _int_range(section: dict[str, Any], key: str, *, default: int, minimum: int, maximum: int) -> int:
    value = section.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ConfigError(f"{key} must be an integer between {minimum} and {maximum}.")
    return value


def _port_value(section: dict[str, Any], key: str, *, default: int) -> int:
    return _int_range(section, key, default=default, minimum=1, maximum=65535)


def _string_list(section: dict[str, Any], key: str, *, default: list[str]) -> list[str]:
    value = section.get(key, default)
    if not isinstance(value, list):
        raise ConfigError(f"{key} must be an array.")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ConfigError(f"{key} must contain non-empty strings.")
        text = item.strip()
        if text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


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


def _rtsp_path(value: str) -> str:
    path = value.strip().lstrip("/")
    if not path:
        raise ConfigError("rtsp_path must be a non-empty path.")
    return path


def _rtsp_transport(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"tcp", "udp"}:
        raise ConfigError("rtsp_transport must be tcp or udp.")
    return normalized


def _operation_vectors(value: Any) -> dict[str, tuple[float, float]]:
    if not isinstance(value, dict):
        raise ConfigError("operation_vectors must be an object.")
    vectors: dict[str, tuple[float, float]] = {}
    for operation in SUPPORTED_OPERATIONS:
        raw_vector = value.get(operation)
        if not isinstance(raw_vector, list | tuple) or len(raw_vector) != 2:
            raise ConfigError(f"operation_vectors.{operation} must be an array of two numbers.")
        x, y = raw_vector
        if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, int | float) or not isinstance(y, int | float):
            raise ConfigError(f"operation_vectors.{operation} must contain numbers.")
        if not -1.0 <= float(x) <= 1.0 or not -1.0 <= float(y) <= 1.0:
            raise ConfigError(f"operation_vectors.{operation} values must be between -1.0 and 1.0.")
        if x == 0 and y == 0:
            raise ConfigError(f"operation_vectors.{operation} must not be [0, 0].")
        vectors[operation] = (float(x), float(y))
    return vectors
