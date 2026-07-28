from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class ServerConfig:
    base_url: str
    access_token: str
    access_token_env: str
    tls_verify: bool
    request_timeout_seconds: float
    reconnect_delay_seconds: float


@dataclass(frozen=True)
class AppConfig:
    server: ServerConfig
    client_id: str


def load_config(
    path: Path | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> AppConfig:
    env = environ if environ is not None else os.environ
    raw = _read_json_config(path)
    if set(raw) - {"server", "connector"}:
        raise ConfigError("config root contains unsupported fields.")
    server = _object(raw.get("server", {}), "server")
    connector = _object(raw.get("connector", {}), "connector")
    allowed_server_fields = {
        "base_url",
        "tls_verify",
        "request_timeout_seconds",
        "reconnect_delay_seconds",
        "access_token",
        "access_token_env",
    }
    if set(server) - allowed_server_fields:
        raise ConfigError("server contains unsupported fields.")
    if set(connector) - {"client_id"}:
        raise ConfigError("connector contains unsupported fields.")

    base_url = _normalize_base_url(
        _string(
            server.get("base_url"),
            default=env.get(
                "OTOMEKAIRO_SERVER_URL",
                "https://127.0.0.1:55601",
            ),
            label="server.base_url",
        )
    )
    access_token, access_token_env = _resolve_access_token(
        server=server,
        environ=env,
    )
    return AppConfig(
        server=ServerConfig(
            base_url=base_url,
            access_token=access_token,
            access_token_env=access_token_env,
            tls_verify=_boolean(
                server.get("tls_verify"),
                default=False,
                label="server.tls_verify",
            ),
            request_timeout_seconds=_positive_number(
                server.get("request_timeout_seconds"),
                default=10.0,
                label="server.request_timeout_seconds",
            ),
            reconnect_delay_seconds=_positive_number(
                server.get("reconnect_delay_seconds"),
                default=5.0,
                label="server.reconnect_delay_seconds",
            ),
        ),
        client_id=_string(
            connector.get("client_id"),
            default="microphone-connector-main",
            label="connector.client_id",
        ),
    )


def _resolve_access_token(
    *,
    server: dict[str, Any],
    environ: Mapping[str, str],
) -> tuple[str, str]:
    direct = server.get("access_token")
    if direct is not None:
        if not isinstance(direct, str) or not direct.strip():
            raise ConfigError("server.access_token must be a non-empty string.")
        return direct.strip(), ""
    env_name = server.get("access_token_env", "OTOMEKAIRO_ACCESS_TOKEN")
    if not isinstance(env_name, str) or not env_name.strip():
        raise ConfigError("server.access_token_env must be a non-empty string.")
    env_token = environ.get(env_name.strip(), "")
    if env_token.strip():
        return env_token.strip(), env_name.strip()

    return find_local_access_token(environ), env_name.strip()


def find_local_access_token(
    environ: Mapping[str, str] | None = None,
) -> str:
    env = environ if environ is not None else os.environ
    data_dir_value = env.get("OTOMEKAIRO_DATA_DIR")
    candidates: list[Path] = []
    if data_dir_value:
        candidates.append(Path(data_dir_value).expanduser() / "config.db")
    repository_root = Path(__file__).resolve().parents[4]
    candidates.append(repository_root / "var" / "otomekairo" / "config.db")
    # 同一ホスト構成では秘密値を別ファイルへ複製せず server DB から読む。
    for db_path in candidates:
        token = _read_config_db_token(db_path)
        if token:
            return token
    return ""


def find_runtime_access_token(
    access_token_env: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    env = environ if environ is not None else os.environ
    env_token = env.get(access_token_env, "")
    if env_token.strip():
        return env_token.strip()
    return find_local_access_token(env)


def _read_config_db_token(path: Path) -> str:
    try:
        with sqlite3.connect(path) as connection:
            row = connection.execute(
                """
                SELECT console_access_token
                FROM server_identity
                WHERE id = 1
                """
            ).fetchone()
    except sqlite3.Error:
        return ""
    value = row[0] if row is not None else None
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _read_json_config(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
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


def _string(value: Any, *, default: str, label: str) -> str:
    resolved = default if value is None else value
    if not isinstance(resolved, str) or not resolved.strip():
        raise ConfigError(f"{label} must be a non-empty string.")
    return resolved.strip()


def _boolean(value: Any, *, default: bool, label: str) -> bool:
    resolved = default if value is None else value
    if not isinstance(resolved, bool):
        raise ConfigError(f"{label} must be a boolean.")
    return resolved


def _positive_number(value: Any, *, default: float, label: str) -> float:
    resolved = default if value is None else value
    if type(resolved) not in {int, float} or float(resolved) <= 0.0:
        raise ConfigError(f"{label} must be a positive number.")
    return float(resolved)


def _normalize_base_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.path not in {"", "/"}:
        raise ConfigError("server.base_url must be an https origin URL.")
    return value.rstrip("/")
