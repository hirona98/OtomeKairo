from __future__ import annotations

from unittest.mock import Mock

from otomekairo.http_server import OtomeKairoHandler


def test_http_response_log_levels(monkeypatch) -> None:
    log = Mock()
    monkeypatch.setattr("otomekairo.http_server.debug_log", log)
    handler = object.__new__(OtomeKairoHandler)
    handler.command = "GET"
    handler.path = "/api/config/watchers/watcher%3Aleika-eye-1/runtime-config"

    handler._debug_log_response(200, {"ok": True, "data": {}})
    handler._debug_log_response(404, {"ok": False, "error": {"code": "not_found"}})
    handler._debug_log_response(500, {"ok": False, "error": {"code": "internal_server_error"}})

    assert log.call_args_list[0].kwargs == {"level": "DEBUG"}
    assert log.call_args_list[1].kwargs == {"level": "WARNING"}
    assert log.call_args_list[2].kwargs == {"level": "ERROR"}


def test_static_response_log_levels(monkeypatch) -> None:
    log = Mock()
    monkeypatch.setattr("otomekairo.http_server.debug_log", log)
    handler = object.__new__(OtomeKairoHandler)
    handler.command = "GET"
    handler.path = "/ui/missing.js"
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock()
    handler.wfile = Mock()

    handler._write_static_response(
        status=404,
        body=b"Not Found",
        content_type="text/plain; charset=utf-8",
        cache_control="no-store",
    )

    assert log.call_args.kwargs == {"level": "WARNING"}
