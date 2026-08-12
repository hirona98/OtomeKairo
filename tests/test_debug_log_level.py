from __future__ import annotations

import pytest

from otomekairo.service import common


def test_debug_log_default_min_level_is_warning(monkeypatch, capsys) -> None:
    monkeypatch.delenv("OTOMEKAIRO_DEBUG_LOG_MIN_LEVEL", raising=False)
    monkeypatch.setattr(common, "_debug_log_file_path", None)
    monkeypatch.setattr(common, "_debug_log_stream_sink", None)
    monkeypatch.setattr(common, "_debug_log_stream_pending_records", [])

    common.debug_log("Test", "debug line", level="DEBUG")
    common.debug_log("Test", "info line", level="INFO")
    common.debug_log("Test", "warning line", level="WARNING")
    common.debug_log("Test", "error line", level="ERROR")

    captured = capsys.readouterr().out
    assert "debug line" not in captured
    assert "info line" not in captured
    assert "warning line" in captured
    assert "error line" in captured
    assert [(record["level"], record["msg"]) for record in common._debug_log_stream_pending_records] == [
        ("WARNING", "warning line"),
        ("ERROR", "error line"),
    ]


def test_debug_log_min_level_debug_emits_all(monkeypatch, capsys) -> None:
    monkeypatch.setenv("OTOMEKAIRO_DEBUG_LOG_MIN_LEVEL", "DEBUG")
    monkeypatch.setattr(common, "_debug_log_file_path", None)
    monkeypatch.setattr(common, "_debug_log_stream_sink", None)
    monkeypatch.setattr(common, "_debug_log_stream_pending_records", [])

    common.debug_log("Test", "debug line", level="DEBUG")
    common.debug_log("Test", "info line", level="INFO")

    captured = capsys.readouterr().out
    assert "debug line" in captured
    assert "info line" in captured
    assert len(common._debug_log_stream_pending_records) == 2


def test_debug_log_min_level_invalid_raises(monkeypatch) -> None:
    monkeypatch.setenv("OTOMEKAIRO_DEBUG_LOG_MIN_LEVEL", "TRACE")
    with pytest.raises(SystemExit, match="OTOMEKAIRO_DEBUG_LOG_MIN_LEVEL"):
        common.debug_log("Test", "should fail", level="ERROR")
