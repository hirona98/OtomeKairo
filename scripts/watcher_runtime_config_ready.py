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
from pathlib import Path

from connector_runtime_config_ready import (  # type: ignore[import-not-found]
    FATAL,
    SKIP,
    START,
    PreflightError,
    RuntimeConfigNotFound,
    _candidate_config_db_paths,
    _env_value,
    _error_code,
    _normalize_base_url,
    _resolve_access_token,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether an OtomeKairo watcher has runtime config.")
    parser.parse_args()

    try:
        settings = load_settings()
        runtime_config = fetch_runtime_config(settings)
        watcher = runtime_config.get("watcher")
        if not isinstance(watcher, dict):
            print("skipping watcher: runtime config has no watcher object.", file=sys.stderr)
            return SKIP
        enabled = watcher.get("enabled") is True
        print(
            f"watcher runtime config found. enabled={str(enabled).lower()} "
            f"watcher_id={settings['watcher_id']}",
            file=sys.stderr,
        )
        return START
    except RuntimeConfigNotFound as exc:
        print(f"skipping watcher: {exc}", file=sys.stderr)
        return SKIP
    except PreflightError as exc:
        print(f"watcher preflight failed: {exc}", file=sys.stderr)
        return FATAL


def load_settings() -> dict[str, object]:
    base_url = _normalize_base_url(_env_value(os.environ, "OTOMEKAIRO_SERVER_URL", "https://127.0.0.1:55601"))
    tls_verify = _env_bool_value("OTOMEKAIRO_TLS_VERIFY", default=False)
    timeout_seconds = _env_positive_float("OTOMEKAIRO_WATCHER_PREFLIGHT_TIMEOUT_SECONDS", default=10.0)
    access_token = _resolve_access_token(
        server={},
        environ=os.environ,
        config_path=None,
        base_url=base_url,
        tls_verify=tls_verify,
        timeout_seconds=timeout_seconds,
    )
    return {
        "base_url": base_url,
        "tls_verify": tls_verify,
        "timeout_seconds": timeout_seconds,
        "access_token": access_token,
        "watcher_id": _configured_watcher_id(),
    }


def fetch_runtime_config(settings: dict[str, object]) -> dict:
    watcher_id = str(settings["watcher_id"])
    path = f"/api/config/watchers/{urllib.parse.quote(watcher_id, safe='')}/runtime-config"
    url = f"{settings['base_url']}{path}"
    context = ssl.create_default_context()
    if settings["tls_verify"] is False:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    request = urllib.request.Request(
        url=url,
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {settings['access_token']}",
        },
    )
    try:
        with urllib.request.urlopen(request, context=context, timeout=float(settings["timeout_seconds"])) as response:
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
    if status_code == 404 and _error_code(envelope) == "watcher_runtime_config_not_found":
        raise RuntimeConfigNotFound(f"{watcher_id} has no runtime config.")
    if status_code >= 400 or envelope.get("ok") is not True:
        code = _error_code(envelope) or "unknown_error"
        raise PreflightError(f"GET {path} failed: HTTP {status_code} {code}.")
    data = envelope.get("data")
    if not isinstance(data, dict):
        raise PreflightError(f"GET {path} returned a non-object data payload.")
    return data


def _configured_watcher_id() -> str:
    configured = _env_value(os.environ, "OTOMEKAIRO_WATCHER_ID", "")
    if configured:
        return _watcher_id(configured)

    discovered_ids: list[str] = []
    for db_path in _candidate_config_db_paths(server={}, environ=os.environ, config_path=None):
        discovered_ids.extend(_registered_watcher_ids_from_config_db(db_path))
    unique_ids = sorted(set(discovered_ids))
    if len(unique_ids) == 1:
        return _watcher_id(unique_ids[0])
    if len(unique_ids) > 1:
        raise PreflightError("watcher.watcher_id must be set when multiple registered watchers exist.")
    raise RuntimeConfigNotFound("no registered watcher in config.db.")


def _registered_watcher_ids_from_config_db(db_path: Path) -> list[str]:
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
        if not isinstance(camera_source, dict):
            continue
        watcher = camera_source.get("watcher")
        if not isinstance(watcher, dict):
            continue
        watcher_id = watcher.get("watcher_id")
        if isinstance(watcher_id, str) and watcher_id.strip():
            watcher_ids.append(watcher_id.strip())
    return watcher_ids


def _watcher_id(value: str) -> str:
    if not value.startswith("watcher:"):
        raise PreflightError("watcher.watcher_id must start with watcher:.")
    return value


def _env_bool_value(key: str, *, default: bool) -> bool:
    value = os.environ.get(key)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise PreflightError(f"{key} must be a boolean.")


def _env_positive_float(key: str, *, default: float) -> float:
    raw_value = os.environ.get(key)
    if raw_value is None or not raw_value.strip():
        return default
    try:
        value = float(raw_value.strip())
    except ValueError as exc:
        raise PreflightError(f"{key} must be a positive number.") from exc
    if value <= 0:
        raise PreflightError(f"{key} must be a positive number.")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
