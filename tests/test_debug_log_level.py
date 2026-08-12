from __future__ import annotations

from pathlib import Path

import pytest

from otomekairo.service import common


def test_debug_log_stdout_min_level_default_is_warning(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.delenv("OTOMEKAIRO_DEBUG_LOG_MIN_LEVEL", raising=False)
    log_path = tmp_path / "server.log"
    monkeypatch.setattr(common, "_debug_log_file_path", log_path)
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

    file_text = log_path.read_text(encoding="utf-8")
    assert "debug line" in file_text
    assert "info line" in file_text
    assert "warning line" in file_text
    assert "error line" in file_text

    assert [(record["level"], record["msg"]) for record in common._debug_log_stream_pending_records] == [
        ("DEBUG", "debug line"),
        ("INFO", "info line"),
        ("WARNING", "warning line"),
        ("ERROR", "error line"),
    ]


def test_debug_log_stdout_min_level_debug_emits_all(monkeypatch, capsys) -> None:
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
