#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
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
    _bool_value,
    _env_value,
    _error_code,
    _normalize_base_url,
    _object,
    _positive_float,
    _read_json_config,
    _resolve_access_token,
    _string_value,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Check whether an OtomeKairo watcher has runtime config.")
    parser.add_argument("--default-watcher-id", required=True)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()

    try:
        settings = load_settings(config_path=args.config, default_watcher_id=args.default_watcher_id)
        runtime_config = fetch_runtime_config(settings)
        watcher = runtime_config.get("watcher")
        if not isinstance(watcher, dict) or watcher.get("enabled") is not True:
            print("skipping watcher: watcher is disabled.", file=sys.stderr)
            return SKIP
        camera_source = runtime_config.get("camera_source")
        if not isinstance(camera_source, dict) or camera_source.get("enabled") is not True:
            print("skipping watcher: camera source is disabled.", file=sys.stderr)
            return SKIP
        print("watcher runtime config found.", file=sys.stderr)
        return START
    except RuntimeConfigNotFound as exc:
        print(f"skipping watcher: {exc}", file=sys.stderr)
        return SKIP
    except PreflightError as exc:
        print(f"watcher preflight failed: {exc}", file=sys.stderr)
        return FATAL


def load_settings(*, config_path: Path | None, default_watcher_id: str) -> dict[str, object]:
    raw = _read_json_config(config_path)
    server = _object(raw.get("server", {}), "server")
    watcher = _object(raw.get("watcher", {}), "watcher")
    base_url = _normalize_base_url(
        _string_value(
            server,
            "base_url",
            default=_env_value(os.environ, "OTOMEKAIRO_SERVER_URL", "https://127.0.0.1:55601"),
        )
    )
    tls_verify = _bool_value(server, "tls_verify", default=False)
    timeout_seconds = _positive_float(server, "request_timeout_seconds", default=10.0)
    access_token = _resolve_access_token(
        server=server,
        environ=os.environ,
        config_path=config_path,
        base_url=base_url,
        tls_verify=tls_verify,
        timeout_seconds=timeout_seconds,
    )
    return {
        "base_url": base_url,
        "tls_verify": tls_verify,
        "timeout_seconds": timeout_seconds,
        "access_token": access_token,
        "watcher_id": _string_value(watcher, "watcher_id", default=default_watcher_id),
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


if __name__ == "__main__":
    raise SystemExit(main())
