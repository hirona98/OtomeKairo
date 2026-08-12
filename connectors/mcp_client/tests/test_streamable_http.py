import unittest
from unittest.mock import patch

from otomekairo_mcp_client_connector.config import (
    ConfigError,
    McpServerConfig,
    _mcp_server_configs,
)
from otomekairo_mcp_client_connector.mcp_bridge import _client_session


class _AsyncContext:
    def __init__(self, value):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _FakeHttpClient(_AsyncContext):
    calls = []

    def __init__(self, **kwargs):
        type(self).calls.append(kwargs)
        super().__init__(self)


class _FakeSession(_AsyncContext):
    initialized = False

    def __init__(self, read, write):
        super().__init__(self)

    async def initialize(self):
        type(self).initialized = True


class StreamableHttpConfigTests(unittest.TestCase):
    def test_remote_config_is_a_strict_transport_variant(self) -> None:
        servers = _mcp_server_configs(
            [
                {
                    "mcp_server_id": "elyth",
                    "connector_kind": "mcp_client",
                    "client_id": "mcp-client-connector-main",
                    "enabled": True,
                    "transport": "streamable_http",
                    "url": "https://elythworld.com/api/mcp/remote",
                    "headers": {"Authorization": "Bearer test-token"},
                }
            ]
        )

        self.assertEqual(servers[0].url, "https://elythworld.com/api/mcp/remote")
        self.assertEqual(servers[0].headers["Authorization"], "Bearer test-token")
        self.assertIsNone(servers[0].command)

    def test_remote_config_rejects_stdio_fields(self) -> None:
        with self.assertRaises(ConfigError):
            _mcp_server_configs(
                [
                    {
                        "mcp_server_id": "elyth",
                        "transport": "streamable_http",
                        "url": "https://elythworld.com/api/mcp/remote",
                        "headers": {},
                        "command": "not-allowed",
                    }
                ]
            )

    def test_remote_config_rejects_protocol_header(self) -> None:
        with self.assertRaises(ConfigError):
            _mcp_server_configs(
                [
                    {
                        "mcp_server_id": "elyth",
                        "transport": "streamable_http",
                        "url": "https://elythworld.com/api/mcp/remote",
                        "headers": {"Mcp-Session-Id": "not-caller-owned"},
                    }
                ]
            )


class StreamableHttpSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_remote_session_uses_headers_without_redirects(self) -> None:
        server = McpServerConfig(
            mcp_server_id="elyth",
            transport="streamable_http",
            command=None,
            args=[],
            env={},
            cwd=None,
            url="https://elythworld.com/api/mcp/remote",
            headers={"Authorization": "Bearer test-token"},
        )
        _FakeHttpClient.calls = []
        _FakeSession.initialized = False
        stream_calls = []

        def fake_streamable_http_client(url, *, http_client):
            stream_calls.append((url, http_client))
            return _AsyncContext(("read", "write", None))

        with (
            patch(
                "otomekairo_mcp_client_connector.mcp_bridge.httpx.AsyncClient",
                _FakeHttpClient,
            ),
            patch(
                "otomekairo_mcp_client_connector.mcp_bridge.streamable_http_client",
                fake_streamable_http_client,
            ),
            patch(
                "otomekairo_mcp_client_connector.mcp_bridge.ClientSession",
                _FakeSession,
            ),
        ):
            async with _client_session(server):
                pass

        self.assertEqual(_FakeHttpClient.calls[0]["headers"], server.headers)
        self.assertFalse(_FakeHttpClient.calls[0]["follow_redirects"])
        self.assertEqual(stream_calls[0][0], server.url)
        self.assertTrue(_FakeSession.initialized)


if __name__ == "__main__":
    unittest.main()
