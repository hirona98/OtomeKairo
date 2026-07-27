import unittest
from copy import deepcopy

from otomekairo.defaults import build_default_state
from otomekairo.event_stream import EventStreamRegistry
from otomekairo.service.common import ServiceError
from otomekairo.service.config.resources import ServiceConfigResourcesMixin
from otomekairo.service.config.stream import ServiceConfigStreamMixin
from otomekairo.service.spontaneous.capability_payload import ServiceSpontaneousCapabilityPayloadMixin


class DummyWebSocket:
    def close(self) -> None:
        return None


class DummyStore:
    def __init__(self) -> None:
        # hello が参照する正規のMCPサーバー定義を保持する。
        self.state = build_default_state()
        mcp_server = self.state["mcp_servers"]["mcp:elyth"]
        mcp_server["enabled"] = True
        mcp_server["enabled_tools"] = ["create_post", "get_information"]

    def read_state(self) -> dict:
        return deepcopy(self.state)


class DummyService(
    ServiceConfigStreamMixin,
    ServiceConfigResourcesMixin,
    ServiceSpontaneousCapabilityPayloadMixin,
):
    def __init__(self) -> None:
        self.store = DummyStore()
        self._event_stream_registry = EventStreamRegistry()

    def _now_iso(self) -> str:
        return "2026-06-20T12:00:00+09:00"

    def _clamp(self, value: str, *, limit: int) -> str:
        return value


class McpCapabilityTests(unittest.TestCase):
    def test_hello_accepts_mcp_server_tools(self) -> None:
        service = DummyService()
        session_id = service.register_event_stream_connection(DummyWebSocket())

        service.handle_event_stream_message(
            session_id,
            {
                "type": "hello",
                "client_id": "mcp-client-connector-main",
                "caps": [{"id": "mcp.call_tool", "version": "1"}],
                "mcp_servers": [
                    {
                        "mcp_server_id": "mcp:elyth",
                        "transport": "stdio",
                        "tools": [
                            {
                                "name": "create_post",
                                "description": "投稿する",
                                "inputSchema": {
                                    "type": "object",
                                    "properties": {"content": {"type": "string"}},
                                    "required": ["content"],
                                    "additionalProperties": False,
                                },
                            }
                        ],
                    }
                ],
            },
        )

        bindings = service._event_stream_registry.list_capability_bindings()
        self.assertEqual(bindings["accepted"]["mcp.call_tool"], ["mcp-client-connector-main"])
        self.assertEqual(bindings["mcp_servers"][0]["mcp_server_id"], "mcp:elyth")

    def test_registry_resolves_mcp_tool_target(self) -> None:
        service = DummyService()
        session_id = service.register_event_stream_connection(DummyWebSocket())
        service.handle_event_stream_message(
            session_id,
            {
                "type": "hello",
                "client_id": "mcp-client-connector-main",
                "caps": [{"id": "mcp.call_tool", "version": "1"}],
                "mcp_servers": [
                    {
                        "mcp_server_id": "mcp:elyth",
                        "transport": "stdio",
                        "tools": [{"name": "get_information", "description": "", "inputSchema": {"type": "object"}}],
                    }
                ],
            },
        )

        target = service._event_stream_registry.get_mcp_tool_target(
            mcp_server_id="mcp:elyth",
            tool_name="get_information",
        )

        self.assertIsNotNone(target)
        self.assertEqual(target["client_id"], "mcp-client-connector-main")
        self.assertEqual(target["tool"]["name"], "get_information")

    def test_hello_rejects_mcp_servers_without_capability(self) -> None:
        service = DummyService()
        session_id = service.register_event_stream_connection(DummyWebSocket())

        with self.assertRaises(ServiceError) as raised:
            service.handle_event_stream_message(
                session_id,
                {
                    "type": "hello",
                    "client_id": "mcp-client-connector-main",
                    "caps": [],
                    "mcp_servers": [
                        {
                            "mcp_server_id": "mcp:elyth",
                            "transport": "stdio",
                            "tools": [{"name": "get_information"}],
                        }
                    ],
                },
            )
        self.assertEqual(raised.exception.error_code, "invalid_mcp_servers")

    def test_hello_rejects_mcp_server_assigned_to_another_client(self) -> None:
        service = DummyService()
        session_id = service.register_event_stream_connection(DummyWebSocket())
        payload = {
            "type": "hello",
            "client_id": "mcp-client-connector-secondary",
            "caps": [{"id": "mcp.call_tool", "version": "1"}],
            "mcp_servers": [
                {
                    "mcp_server_id": "mcp:elyth",
                    "transport": "stdio",
                    "tools": [{"name": "get_information", "description": "", "inputSchema": {"type": "object"}}],
                }
            ],
        }

        with self.assertRaises(ServiceError) as raised:
            service.handle_event_stream_message(session_id, payload)

        self.assertEqual(raised.exception.error_code, "invalid_mcp_servers")
        self.assertIn("assigned to another client_id", raised.exception.message)

    def test_hello_rejects_tool_not_enabled_in_server_definition(self) -> None:
        service = DummyService()
        service.store.state["mcp_servers"]["mcp:elyth"]["enabled_tools"] = ["get_information"]
        session_id = service.register_event_stream_connection(DummyWebSocket())

        with self.assertRaises(ServiceError) as raised:
            service.handle_event_stream_message(
                session_id,
                {
                    "type": "hello",
                    "client_id": "mcp-client-connector-main",
                    "caps": [{"id": "mcp.call_tool", "version": "1"}],
                    "mcp_servers": [
                        {
                            "mcp_server_id": "mcp:elyth",
                            "transport": "stdio",
                            "tools": [
                                {
                                    "name": "create_post",
                                    "description": "投稿する",
                                    "inputSchema": {"type": "object"},
                                }
                            ],
                        }
                    ],
                },
            )

        self.assertEqual(raised.exception.error_code, "invalid_mcp_servers")
        self.assertIn("tool that is not enabled", raised.exception.message)

    def test_mcp_call_tool_result_drops_raw_content(self) -> None:
        service = DummyService()

        payload = service._normalize_mcp_call_tool_result_payload(
            result_payload={
                "status": "completed",
                "mcp_server_id": "mcp:elyth",
                "tool_name": "get_information",
                "is_error": False,
                "content": [{"type": "text", "text": "raw result"}],
                "structured_content": {"raw": "value"},
                "client_context": {"mcp_result_summary": "要約"},
                "error": None,
            }
        )

        self.assertEqual(payload["content"], [])
        self.assertIsNone(payload["structured_content"])
        self.assertEqual(payload["client_context"]["mcp_content_item_count"], 1)
        self.assertTrue(payload["client_context"]["mcp_structured_content_present"])


if __name__ == "__main__":
    unittest.main()
