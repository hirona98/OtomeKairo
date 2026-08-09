from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "connectors" / "tapo_c220" / "src"))

import debug_tapo_c220_connector as debug_connector  # noqa: E402


def test_missing_camera_source_returns_skip_without_retry(monkeypatch) -> None:
    calls = 0

    def runtime_config_ready() -> int:
        nonlocal calls
        calls += 1
        return debug_connector.SKIP

    monkeypatch.setattr(debug_connector, "_runtime_config_ready", runtime_config_ready)

    assert debug_connector._wait_for_runtime_config() == debug_connector.SKIP
    assert calls == 1


def test_preflight_error_remains_fatal_after_retry_timeout(monkeypatch) -> None:
    times = iter((100.0, 131.0))
    monkeypatch.setattr(debug_connector, "_runtime_config_ready", lambda: debug_connector.FATAL)
    monkeypatch.setattr(debug_connector.time, "monotonic", lambda: next(times))

    assert debug_connector._wait_for_runtime_config() == debug_connector.FATAL
