from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    base_url: str
    config_db_path: Path
    tls_verify: bool
    request_timeout_seconds: float
    client_id: str
    sensor_source_id: str
    serial_port: str
    poll_interval_seconds: float
    serial_timeout_seconds: float

    def read_access_token(self) -> str:
        try:
            with sqlite3.connect(self.config_db_path.as_uri() + "?mode=ro", uri=True) as connection:
                row = connection.execute("SELECT console_access_token FROM server_identity WHERE id=1").fetchone()
        except sqlite3.Error as exc:
            raise ConfigError("could not read server token from config_db_path") from exc
        if row is None or not isinstance(row[0], str) or not row[0].strip():
            raise ConfigError("server token is missing from config_db_path")
        return row[0]


def _keys(value, expected: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        raise ConfigError(f"{label} must contain exactly: {', '.join(sorted(expected))}")
    return value


def _text(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{label} must be a non-empty string")
    return value.strip()


def _positive(value, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{label} must be a finite positive number")
    return float(value)


def load_config(path: Path) -> Config:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError("could not read connector JSON config") from exc
    _keys(raw, {"server", "connector"}, "config")
    server = _keys(raw["server"], {"base_url", "config_db_path", "tls_verify", "request_timeout_seconds"}, "server")
    connector = _keys(raw["connector"], {"client_id", "sensor_source_id", "serial_port",
                                       "poll_interval_seconds", "serial_timeout_seconds"}, "connector")
    base_url = _text(server["base_url"], "base_url")
    parsed = urlparse(base_url)
    if (parsed.scheme != "https" or not parsed.netloc or parsed.username is not None
            or parsed.password is not None or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise ConfigError("base_url must be an HTTPS origin without credentials")
    if type(server["tls_verify"]) is not bool:
        raise ConfigError("tls_verify must be a boolean")
    source_id = _text(connector["sensor_source_id"], "sensor_source_id")
    if not source_id.startswith("sensor_source:") or source_id == "sensor_source:":
        raise ConfigError("sensor_source_id must start with sensor_source:")
    serial_port = _text(connector["serial_port"], "serial_port")
    if not Path(serial_port).is_absolute():
        raise ConfigError("serial_port must be an explicit absolute device path")
    db_path = Path(_text(server["config_db_path"], "config_db_path")).expanduser()
    if not db_path.is_absolute():
        db_path = path.resolve().parent / db_path
    return Config(base_url.rstrip("/"), db_path.resolve(), server["tls_verify"],
                  _positive(server["request_timeout_seconds"], "request_timeout_seconds"),
                  _text(connector["client_id"], "client_id"), source_id, serial_port,
                  _positive(connector["poll_interval_seconds"], "poll_interval_seconds"),
                  _positive(connector["serial_timeout_seconds"], "serial_timeout_seconds"))
