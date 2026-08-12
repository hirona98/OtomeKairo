from __future__ import annotations

import pytest

from otomekairo_tapo_c220_watcher import log as watcher_log


def test_watcher_emit_log_default_min_level_is_warning(monkeypatch, capsys) -> None:
    monkeypatch.delenv("OTOMEKAIRO_DEBUG_LOG_MIN_LEVEL", raising=False)

    watcher_log.emit_log("tapo-c220-watcher", "diff result action=none", level="DEBUG")
    watcher_log.emit_log("tapo-c220-watcher", "wake posted", level="INFO")
    watcher_log.emit_log("tapo-c220-watcher", "stream disconnected", level="WARNING")
    watcher_log.emit_log("tapo-c220-watcher", "watch loop failed", level="ERROR")

    captured = capsys.readouterr().err
    assert "diff result" not in captured
    assert "wake posted" not in captured
    assert "stream disconnected" in captured
    assert "watch loop failed" in captured


def test_watcher_emit_log_debug_emits_all(monkeypatch, capsys) -> None:
    monkeypatch.setenv("OTOMEKAIRO_DEBUG_LOG_MIN_LEVEL", "DEBUG")

    watcher_log.emit_log("tapo-c220-watcher", "diff result action=none", level="DEBUG")

    captured = capsys.readouterr().err
    assert "diff result action=none" in captured


def test_watcher_emit_log_invalid_min_level_raises(monkeypatch) -> None:
    monkeypatch.setenv("OTOMEKAIRO_DEBUG_LOG_MIN_LEVEL", "TRACE")
    with pytest.raises(SystemExit, match="OTOMEKAIRO_DEBUG_LOG_MIN_LEVEL"):
        watcher_log.emit_log("tapo-c220-watcher", "should fail", level="ERROR")
