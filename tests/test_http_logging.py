from __future__ import annotations

from unittest.mock import Mock

from otomekairo.http_server import (
    OtomeKairoHandler,
    OtomeKairoHttpServer,
    is_client_disconnect,
)


def test_is_client_disconnect_recognizes_connection_reset() -> None:
    assert is_client_disconnect(ConnectionResetError(104, "Connection reset by peer"))
    assert is_client_disconnect(BrokenPipeError())
    assert not is_client_disconnect(ValueError("not a disconnect"))


def test_handle_error_suppresses_client_disconnect_traceback(monkeypatch) -> None:
    server = object.__new__(OtomeKairoHttpServer)
    super_handle_error = Mock()
    monkeypatch.setattr(
        "http.server.ThreadingHTTPServer.handle_error",
        super_handle_error,
    )

    try:
        raise ConnectionResetError(104, "Connection reset by peer")
    except ConnectionResetError:
        server.handle_error(None, ("127.0.0.1", 12345))

    super_handle_error.assert_not_called()

    try:
        raise RuntimeError("unexpected failure")
    except RuntimeError:
        server.handle_error(None, ("127.0.0.1", 12345))

    super_handle_error.assert_called_once()


def test_http_response_log_levels(monkeypatch) -> None:
    log = Mock()
    monkeypatch.setattr("otomekairo.http_server.debug_log", log)
    handler = object.__new__(OtomeKairoHandler)
    handler.command = "GET"
    # 高頻度抑制対象ではない通常 API でレベル判定を確認する。
    handler.path = "/api/wake"

    handler._debug_log_response(200, {"ok": True, "data": {}})
    handler._debug_log_response(404, {"ok": False, "error": {"code": "not_found"}})
    handler._debug_log_response(500, {"ok": False, "error": {"code": "internal_server_error"}})

    assert len(log.call_args_list) == 2
    assert log.call_args_list[0].kwargs == {"level": "WARNING"}
    assert log.call_args_list[1].kwargs == {"level": "ERROR"}


def test_watcher_runtime_config_http_log_suppressed(monkeypatch) -> None:
    log = Mock()
    monkeypatch.setattr("otomekairo.http_server.debug_log", log)
    handler = object.__new__(OtomeKairoHandler)
    handler.command = "GET"
    handler.path = "/api/config/watchers/watcher%3A%E5%AF%BE%E9%9D%A2%E3%82%AB%E3%83%A1%E3%83%A9/runtime-config"

    handler._debug_log_response(200, {"ok": True, "data": {}})
    handler._debug_log_response(404, {"ok": False, "error": {"code": "not_found"}})

    assert log.call_args_list == []


def test_polled_config_http_log_suppressed(monkeypatch) -> None:
    log = Mock()
    monkeypatch.setattr("otomekairo.http_server.debug_log", log)
    handler = object.__new__(OtomeKairoHandler)
    handler.command = "GET"

    for path in (
        "/api/config",
        "/api/config/camera-sources",
        "/api/config/connectors/tapo-c220-connector-main/runtime-config",
    ):
        log.reset_mock()
        handler.path = path
        handler._debug_log_response(200, {"ok": True, "data": {}})
        handler._debug_log_response(404, {"ok": False, "error": {"code": "not_found"}})
        assert log.call_args_list == [], path
        assert handler._should_log_http_path(path) is False


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
