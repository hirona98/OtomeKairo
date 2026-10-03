import asyncio
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import AsyncMock, patch

import httpx
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from otomekairo_mcp_client_connector.__main__ import main
from otomekairo_mcp_client_connector.app import McpClientConnector
from otomekairo_mcp_client_connector.config import AppConfig, McpServerConfig, ServerConfig
from otomekairo_mcp_client_connector.mcp_bridge import (
    McpSessionError,
    _client_session,
    call_tool,
    list_tools,
)


def _remote_server():
    return McpServerConfig(
        mcp_server_id="test-server",
        transport="streamable_http",
        command=None,
        args=[],
        env={},
        cwd=None,
        url="https://mcp.example.test/remote?private-query=fixture-only",
        headers={"Authorization": "Bearer fixture-only"},
    )


def _app_config():
    return AppConfig(
        server=ServerConfig(
            base_url="https://localhost:55601",
            access_token="fixture-only",
            tls_verify=True,
            request_timeout_seconds=30.0,
            reconnect_delay_seconds=5.0,
        ),
        client_id="test-connector",
        mcp_servers=[_remote_server()],
    )


def _rpc_response(request, message, result):
    return httpx.Response(
        200,
        request=request,
        json={"jsonrpc": "2.0", "id": message["id"], "result": result},
    )


def _http_client_patch(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return patch(
        "otomekairo_mcp_client_connector.mcp_bridge.httpx.AsyncClient",
        return_value=client,
    )


class McpSessionFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_http_failure_reports_status_without_private_data(self):
        for status in (401, 503):
            with self.subTest(status=status):
                requests = []

                def handler(request):
                    requests.append(json.loads(request.content)["method"])
                    return httpx.Response(status, request=request, text="fixture-only")

                with _http_client_patch(handler):
                    with self.assertRaises(McpSessionError) as raised:
                        await list_tools(_remote_server())

                message = str(raised.exception)
                self.assertIn("server=test-server", message)
                self.assertIn("transport=streamable_http", message)
                self.assertIn(f"HTTPStatusError(HTTP {status})", message)
                self.assertNotIn("fixture-only", message)
                self.assertNotIn("example.test", message)
                self.assertIsInstance(raised.exception.__cause__, ExceptionGroup)
                self.assertEqual(requests, ["initialize"])

    async def test_tool_request_http_failure_uses_the_same_boundary(self):
        for operation in ("tools/list", "tools/call"):
            with self.subTest(operation=operation):
                requests = []

                def handler(request):
                    message = json.loads(request.content)
                    method = message["method"]
                    requests.append(method)
                    if method == "initialize":
                        return _rpc_response(request, message, {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "test-server", "version": "1"},
                        })
                    if method == "notifications/initialized":
                        return httpx.Response(202, request=request)
                    return httpx.Response(503, request=request, text="fixture-only")

                with _http_client_patch(handler):
                    with self.assertRaises(McpSessionError) as raised:
                        if operation == "tools/list":
                            await list_tools(_remote_server())
                        else:
                            await call_tool(_remote_server(), tool_name="test-tool", arguments={})

                self.assertIn("HTTPStatusError(HTTP 503)", str(raised.exception))
                self.assertEqual(requests.count(operation), 1)

    async def test_connection_failure_reports_exception_type(self):
        def handler(request):
            raise httpx.ConnectError("fixture-only", request=request)

        with _http_client_patch(handler):
            with self.assertRaises(McpSessionError) as raised:
                await list_tools(_remote_server())

        self.assertIn("ConnectError", str(raised.exception))
        self.assertNotIn("fixture-only", str(raised.exception))

    async def test_stdio_start_failure_uses_the_same_boundary(self):
        server = McpServerConfig(
            mcp_server_id="test-stdio",
            transport="stdio",
            command="test-launcher",
            args=[],
            env={},
            cwd=None,
            url=None,
            headers={},
        )
        with patch(
            "otomekairo_mcp_client_connector.mcp_bridge.stdio_client",
            side_effect=FileNotFoundError("fixture-only"),
        ):
            with self.assertRaises(McpSessionError) as raised:
                await list_tools(server)

        self.assertIn("server=test-stdio", str(raised.exception))
        self.assertIn("FileNotFoundError", str(raised.exception))
        self.assertNotIn("fixture-only", str(raised.exception))

    async def test_nested_group_preserves_all_machine_readable_causes(self):
        error = ExceptionGroup("fixture-only", [
            httpx.ConnectError("fixture-only"),
            ExceptionGroup("fixture-only", [
                McpError(ErrorData(code=-32000, message="fixture-only")),
            ]),
        ])
        session = AsyncMock()
        session.__aenter__.return_value = session
        session.__aexit__.return_value = False
        session.initialize.side_effect = error

        with (
            _http_client_patch(lambda request: httpx.Response(202, request=request)),
            patch("otomekairo_mcp_client_connector.mcp_bridge.ClientSession", return_value=session),
        ):
            with self.assertRaises(McpSessionError) as raised:
                async with _client_session(_remote_server()):
                    self.fail("failed initialization must not yield a session")

        self.assertIn("ConnectError; McpError(code=-32000)", str(raised.exception))
        self.assertNotIn("fixture-only", str(raised.exception))

    async def test_external_cancellation_propagates(self):
        started = asyncio.Event()

        async def handler(request):
            started.set()
            await asyncio.Event().wait()

        with _http_client_patch(handler):
            task = asyncio.create_task(list_tools(_remote_server()))
            try:
                await asyncio.wait_for(started.wait(), timeout=1.0)
            finally:
                task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task


class McpConnectorFailureTests(unittest.TestCase):
    def test_catalog_failure_retries_before_sending_hello(self):
        connector = McpClientConnector(_app_config())
        failure = McpSessionError(
            "MCP server=test-server transport=streamable_http failed: HTTPStatusError(HTTP 503)"
        )
        with (
            patch("otomekairo_mcp_client_connector.app.list_tools", AsyncMock(side_effect=[failure, []])) as fetch,
            patch("otomekairo_mcp_client_connector.app.EventStreamClient") as stream,
            patch("otomekairo_mcp_client_connector.app.emit_log") as log,
            patch("otomekairo_mcp_client_connector.app.time.sleep") as sleep,
        ):
            stream.return_value.run.side_effect = KeyboardInterrupt
            with self.assertRaises(KeyboardInterrupt):
                connector.run_forever()

        self.assertEqual(fetch.await_count, 2)
        sleep.assert_called_once_with(5.0)
        log.assert_called_once()
        self.assertIn("HTTP 503", log.call_args.args[1])
        stream.assert_called_once()
        stream.return_value.run.assert_called_once()

    def test_print_hello_failure_returns_nonzero_without_a_catalog(self):
        failure = McpSessionError(
            "MCP server=test-server transport=streamable_http failed: HTTPStatusError(HTTP 503)"
        )
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            patch("sys.argv", ["mcp-client-connector", "--print-hello"]),
            patch("otomekairo_mcp_client_connector.__main__.load_config", return_value=_app_config()),
            patch("otomekairo_mcp_client_connector.app.list_tools", AsyncMock(side_effect=failure)),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = main()

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("HTTP 503", stderr.getvalue())
        self.assertNotIn("fixture-only", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
