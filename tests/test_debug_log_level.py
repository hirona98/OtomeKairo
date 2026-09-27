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


def test_format_debug_log_text_error_keeps_full_text() -> None:
    text = "HEAD\n" + ("あ" * 400) + "TAIL"
    formatted = common.format_debug_log_text(text, level="ERROR")
    assert formatted.startswith("HEAD ")
    assert formatted.endswith("TAIL")
    assert "\n" not in formatted
    assert "…" not in formatted


def test_format_debug_log_text_warning_stays_limited() -> None:
    formatted = common.format_debug_log_text("a" * 300, level="WARNING", limit=160)
    assert len(formatted) == 160
    assert formatted.endswith("…")
    assert "a" * 300 not in formatted


def test_format_debug_log_text_non_error_requires_positive_limit() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        common.format_debug_log_text("text", level="INFO")
    with pytest.raises(ValueError, match="positive integer"):
        common.format_debug_log_text("text", level="WARNING", limit=True)  # type: ignore[arg-type]


def test_format_debug_log_text_rejects_unknown_level() -> None:
    with pytest.raises(ValueError, match="DEBUG, INFO, WARNING, ERROR"):
        common.format_debug_log_text("text", level="TRACE")


def test_input_failure_error_log_keeps_full_reason() -> None:
    from otomekairo.service.input.logging import ServiceInputLoggingMixin

    class Host(ServiceInputLoggingMixin):
        def _short_cycle_id(self, cycle_id: str) -> str:
            return "abc"

        def _now_iso(self) -> str:
            return "2026-09-27T12:00:00+09:00"

        def _emit_live_logs(self, logs: list[dict]) -> None:
            self.logs = logs

    host = Host()
    host._emit_input_failure_logs(
        cycle_id="cycle:abc",
        trigger_kind="user_message",
        input_text="hello",
        failure_reason="HEAD\n" + ("あ" * 400) + "TAIL",
    )
    message = host.logs[0]["msg"]
    assert host.logs[0]["level"] == "ERROR"
    assert message.startswith("abc internal_failure reason=HEAD ")
    assert message.endswith("TAIL")
    assert "\n" not in message
