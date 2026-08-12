#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


START = 0
SKIP = 10
FATAL = 2


@dataclass(frozen=True)
class ConnectorSettings:
    base_url: str
    access_token: str
    tls_verify: bool
    timeout_seconds: float
    client_id: str


class PreflightError(RuntimeError):
    pass


class RuntimeConfigNotFound(RuntimeError):
    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether an OtomeKairo connector has runtime config.")
    parser.add_argument("--connector-kind", choices=("tapo_c220", "mcp_client"), required=True)
    parser.add_argument("--default-client-id", required=True)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    try:
        settings = load_settings(
            config_path=args.config,
            default_client_id=args.default_client_id,
            environ=os.environ,
        )
        runtime_config = fetch_runtime_config(settings)
        return decide_start(args.connector_kind, runtime_config)
    except RuntimeConfigNotFound as exc:
        print(f"skipping connector: {exc}", file=sys.stderr)
        return SKIP
    except PreflightError as exc:
        print(f"connector preflight failed: {exc}", file=sys.stderr)
        return FATAL


def load_settings(
    *,
    config_path: Path | None,
    default_client_id: str,
    environ: Mapping[str, str],
) -> ConnectorSettings:
    raw = _read_json_config(config_path)
    server = _object(raw.get("server", {}), "server")
    connector = _object(raw.get("connector", {}), "connector")
    base_url = _normalize_base_url(
        _string_value(
            server,
            "base_url",
            default=_env_value(environ, "OTOMEKAIRO_SERVER_URL", "https://127.0.0.1:55601"),
        )
    )
    tls_verify = _bool_value(server, "tls_verify", default=False)
    timeout_seconds = _positive_float(server, "request_timeout_seconds", default=10.0)
    access_token = _resolve_access_token(
        server=server,
        environ=environ,
        config_path=config_path,
        base_url=base_url,
        tls_verify=tls_verify,
        timeout_seconds=timeout_seconds,
    )
    return ConnectorSettings(
        base_url=base_url,
        access_token=access_token,
        tls_verify=tls_verify,
        timeout_seconds=timeout_seconds,
        client_id=_string_value(connector, "client_id", default=default_client_id),
    )


def fetch_runtime_config(settings: ConnectorSettings) -> dict[str, Any]:
    path = f"/api/config/connectors/{urllib.parse.quote(settings.client_id, safe='')}/runtime-config"
    url = f"{settings.base_url}{path}"
    context = ssl.create_default_context()
    if not settings.tls_verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(
        url=url,
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {settings.access_token}",
        },
    )
    try:
        with urllib.request.urlopen(request, context=context, timeout=settings.timeout_seconds) as response:
            status_code = int(response.getcode())
            raw_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        status_code = int(exc.code)
        raw_body = exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        raise PreflightError(f"GET {path} failed: {exc.reason}") from exc

    try:
        envelope = json.loads(raw_body)
    except json.JSONDecodeError as exc:
        raise PreflightError(f"GET {path} returned invalid JSON.") from exc
    if not isinstance(envelope, dict):
        raise PreflightError(f"GET {path} returned a non-object envelope.")
    if status_code == 404 and _error_code(envelope) == "connector_runtime_config_not_found":
        raise RuntimeConfigNotFound(f"{settings.client_id} has no enabled runtime config.")
    if status_code >= 400 or envelope.get("ok") is not True:
        code = _error_code(envelope) or "unknown_error"
        raise PreflightError(f"GET {path} failed: HTTP {status_code} {code}.")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise PreflightError(f"GET {path} returned a non-object data payload.")
    return data


def decide_start(connector_kind: str, runtime_config: Mapping[str, Any]) -> int:
    if connector_kind == "tapo_c220":
        sources = [
            source
            for source in _list_value(runtime_config.get("camera_sources"))
            if isinstance(source, dict) and source.get("connector_kind") == "tapo_c220"
        ]
        if not sources:
            print("skipping Tapo C220 connector: no enabled tapo_c220 camera source.", file=sys.stderr)
            return SKIP
        if len(sources) != 1:
            raise PreflightError("Tapo C220 connector requires exactly one enabled tapo_c220 camera source.")
        print("Tapo C220 connector runtime config found.", file=sys.stderr)
        return START

    if connector_kind == "mcp_client":
        servers = [
            server
            for server in _list_value(runtime_config.get("mcp_servers"))
            if isinstance(server, dict) and server.get("connector_kind") == "mcp_client"
        ]
        if not servers:
            print("skipping MCP client connector: no enabled mcp_client server.", file=sys.stderr)
            return SKIP
        print(f"MCP client connector runtime config found: count={len(servers)}.", file=sys.stderr)
        return START

    raise PreflightError(f"unsupported connector kind: {connector_kind}")


def _read_json_config(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except json.JSONDecodeError as exc:
        raise PreflightError(f"config file is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise PreflightError("config root must be an object.")
    return payload


def _resolve_access_token(
    *,
    server: Mapping[str, Any],
    environ: Mapping[str, str],
    config_path: Path | None,
    base_url: str,
    tls_verify: bool,
    timeout_seconds: float,
) -> str:
    explicit_token = _secret_value(
        server,
        "access_token",
        "access_token_env",
        default_env="OTOMEKAIRO_ACCESS_TOKEN",
        environ=environ,
    )
    if explicit_token:
        return explicit_token

    for db_path in _candidate_config_db_paths(server=server, environ=environ, config_path=config_path):
        token = _read_config_db_access_token(db_path)
        if token:
            return token

    token = _acquire_console_access_token(
        base_url=base_url,
        tls_verify=tls_verify,
        timeout_seconds=timeout_seconds,
    )
    if token:
        return token
    raise PreflightError("access_token could not be resolved from environment, local config.db, or bootstrap.")


def _candidate_config_db_paths(
    *,
    server: Mapping[str, Any],
    environ: Mapping[str, str],
    config_path: Path | None,
) -> list[Path]:
    # 明示された DB は探索範囲そのものとして扱い、別環境の DB を混在させない。
    config_db_path = server.get("config_db_path")
    if isinstance(config_db_path, str) and config_db_path.strip():
        return [_resolve_config_relative_path(config_db_path.strip(), config_path)]

    data_dirs: list[Path] = []
    env_data_dir = environ.get("OTOMEKAIRO_DATA_DIR")
    if isinstance(env_data_dir, str) and env_data_dir.strip():
        data_dirs.append(Path(env_data_dir.strip()).expanduser())
    data_dir = server.get("data_dir")
    if isinstance(data_dir, str) and data_dir.strip():
        data_dirs.append(_resolve_config_relative_path(data_dir.strip(), config_path))

    if data_dirs:
        return _deduplicate_paths([data_dir / "config.db" for data_dir in data_dirs])

    # 保存先が未指定の場合だけ repository の既定位置を探索する。
    repo_root = Path(__file__).resolve().parents[1]
    return _deduplicate_paths(
        [
            repo_root / "var" / "otomekairo" / "config.db",
            Path.cwd() / "var" / "otomekairo" / "config.db",
        ]
    )


def _read_config_db_access_token(db_path: Path) -> str:
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
        return ""
    token = row[0] if row is not None else None
    return token.strip() if isinstance(token, str) and token.strip() else ""


def _acquire_console_access_token(*, base_url: str, tls_verify: bool, timeout_seconds: float) -> str:
    context = ssl.create_default_context()
    if not tls_verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(
        url=f"{base_url}/api/bootstrap/acquire-console-access-token",
        data=b"{}",
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, context=context, timeout=timeout_seconds) as response:
            raw_body = response.read().decode("utf-8")
    except urllib.error.URLError:
        return ""
    try:
        envelope = json.loads(raw_body)
    except json.JSONDecodeError:
        return ""
    if not isinstance(envelope, dict) or envelope.get("ok") is not True:
        return ""
    data = envelope.get("data")
    if not isinstance(data, dict):
        return ""
    token = data.get("console_access_token")
    return token.strip() if isinstance(token, str) and token.strip() else ""


def _object(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise PreflightError(f"{label} must be an object.")
    return value


def _string_value(payload: Mapping[str, Any], key: str, *, default: str) -> str:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise PreflightError(f"{key} must be a non-empty string.")
    return value.strip()


def _env_value(environ: Mapping[str, str], key: str, default: str) -> str:
    value = environ.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else default


def _bool_value(payload: Mapping[str, Any], key: str, *, default: bool) -> bool:
    value = payload.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    raise PreflightError(f"{key} must be a boolean.")


def _positive_float(payload: Mapping[str, Any], key: str, *, default: float) -> float:
    value = payload.get(key)
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    raise PreflightError(f"{key} must be a positive number.")


def _secret_value(
    payload: Mapping[str, Any],
    value_key: str,
    env_key: str,
    *,
    default_env: str,
    environ: Mapping[str, str],
) -> str:
    explicit = payload.get(value_key)
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    configured_env = payload.get(env_key)
    env_name = configured_env.strip() if isinstance(configured_env, str) and configured_env.strip() else default_env
    return _env_value(environ, env_name, "")


def _normalize_base_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PreflightError("server.base_url must be an absolute http or https URL.")
    return value.rstrip("/")


def _resolve_config_relative_path(value: str, config_path: Path | None) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute() or config_path is None:
        return path
    return config_path.parent / path


def _deduplicate_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    result: list[Path] = []
    for path in paths:
        normalized = path.resolve() if path.exists() else path
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(path)
    return result


def _error_code(envelope: Mapping[str, Any]) -> str:
    error = envelope.get("error")
    if not isinstance(error, dict):
        return ""
    code = error.get("code")
    return code if isinstance(code, str) else ""


def _list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


if __name__ == "__main__":
    raise SystemExit(main())
