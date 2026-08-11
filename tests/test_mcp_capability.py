import unittest
from copy import deepcopy
from types import SimpleNamespace

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
        mcp_server = self.state["mcp_servers"]["e-stat"]
        mcp_server["enabled"] = True

    def read_state(self) -> dict:
        return deepcopy(self.state)

    def list_autonomous_runs(self, *, memory_set_id: str, limit: int) -> list[dict]:
        return []


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
    def _configure_elyth(self, service: DummyService) -> None:
        service.store.state["mcp_servers"] = {
            "elyth": {
                "mcp_server_id": "elyth",
                "connector_kind": "mcp_client",
                "client_id": "mcp-client-connector-main",
                "enabled": True,
                "pre_send_check_enabled": True,
                "transport": "streamable_http",
                "url": "https://elythworld.com/api/mcp/remote",
                "headers": {"Authorization": "Bearer test-token"},
                "skill_bundle_id": "elyth-remote-mcp-skills@0.1.0",
                "autonomous_session": {
                    "enabled": True,
                    "entry_skill": "elyth-run-session",
                    "min_interval_seconds": 3600,
                    "max_tool_calls": 10,
                    "max_mutating_calls": 3,
                },
            }
        }

    def test_hello_accepts_mcp_server_tools(self) -> None:
        service = DummyService()
        session_id = service.register_event_stream_connection(DummyWebSocket())

        service.handle_event_stream_message(
            session_id,
            {
                "type": "hello",
                "client_id": "mcp-client-connector-main",
                "client_kind": "capability_connector",
                "caps": [{"id": "mcp.call_tool", "version": "1"}],
                "mcp_servers": [
                    {
                        "mcp_server_id": "e-stat",
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
        self.assertEqual(bindings["mcp_servers"][0]["mcp_server_id"], "e-stat")

    def test_registry_resolves_mcp_tool_target(self) -> None:
        service = DummyService()
        session_id = service.register_event_stream_connection(DummyWebSocket())
        service.handle_event_stream_message(
            session_id,
            {
                "type": "hello",
                "client_id": "mcp-client-connector-main",
                "client_kind": "capability_connector",
                "caps": [{"id": "mcp.call_tool", "version": "1"}],
                "mcp_servers": [
                    {
                        "mcp_server_id": "e-stat",
                        "transport": "stdio",
                        "tools": [{"name": "get_information", "description": "", "inputSchema": {"type": "object"}}],
                    }
                ],
            },
        )

        target = service._event_stream_registry.get_mcp_tool_target(
            mcp_server_id="e-stat",
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
                    "client_kind": "capability_connector",
                    "caps": [],
                    "mcp_servers": [
                        {
                            "mcp_server_id": "e-stat",
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
            "client_kind": "capability_connector",
            "caps": [{"id": "mcp.call_tool", "version": "1"}],
            "mcp_servers": [
                {
                    "mcp_server_id": "e-stat",
                    "transport": "stdio",
                    "tools": [{"name": "get_information", "description": "", "inputSchema": {"type": "object"}}],
                }
            ],
        }

        with self.assertRaises(ServiceError) as raised:
            service.handle_event_stream_message(session_id, payload)

        self.assertEqual(raised.exception.error_code, "invalid_mcp_servers")
        self.assertIn("assigned to another client_id", raised.exception.message)

    def test_hello_rejects_disabled_mcp_server(self) -> None:
        service = DummyService()
        service.store.state["mcp_servers"]["e-stat"]["enabled"] = False
        session_id = service.register_event_stream_connection(DummyWebSocket())

        with self.assertRaises(ServiceError) as raised:
            service.handle_event_stream_message(
                session_id,
                {
                    "type": "hello",
                    "client_id": "mcp-client-connector-main",
                    "client_kind": "capability_connector",
                    "caps": [{"id": "mcp.call_tool", "version": "1"}],
                    "mcp_servers": [
                        {
                            "mcp_server_id": "e-stat",
                            "transport": "stdio",
                            "tools": [
                                {
                                    "name": "search_e_stat_tables",
                                    "description": "統計表を検索する",
                                    "inputSchema": {"type": "object"},
                                }
                            ],
                        }
                    ],
                },
            )

        self.assertEqual(raised.exception.error_code, "invalid_mcp_servers")
        self.assertIn("not an enabled MCP server definition", raised.exception.message)

    def test_mcp_call_tool_result_drops_raw_content(self) -> None:
        service = DummyService()

        payload = service._normalize_mcp_call_tool_result_payload(
            result_payload={
                "status": "completed",
                "mcp_server_id": "e-stat",
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

    def test_skill_bundle_exposes_only_declared_tools_with_skill_metadata(self) -> None:
        service = DummyService()
        self._configure_elyth(service)

        servers = service._inspection_mcp_servers(
            [
                {
                    "mcp_server_id": "elyth",
                    "client_id": "mcp-client-connector-main",
                    "transport": "streamable_http",
                    "available": True,
                    "tools": [
                        {"name": "create_post", "description": "投稿", "inputSchema": {"type": "object"}},
                        {"name": "undeclared_tool", "description": "不明", "inputSchema": {"type": "object"}},
                    ],
                }
            ]
        )

        self.assertEqual([tool["name"] for tool in servers[0]["tools"]], ["create_post"])
        self.assertEqual(servers[0]["tools"][0]["skill_id"], "elyth-post")
        self.assertTrue(servers[0]["tools"][0]["mutating"])
        self.assertEqual(servers[0]["skill_bundle"]["session_skill"], "elyth-run-session")
        self.assertTrue(service._mcp_tool_is_enabled("elyth", "create_post"))
        self.assertFalse(service._mcp_tool_is_enabled("elyth", "undeclared_tool"))

    def test_selected_skill_loads_root_and_action_text_without_fallback(self) -> None:
        service = DummyService()
        self._configure_elyth(service)
        service.llm = SimpleNamespace(
            generate_operational_skill_selection=lambda **_: {
                "selection": {
                    "mcp_server_id": "elyth",
                    "bundle_id": "elyth-remote-mcp-skills@0.1.0",
                    "skill_id": "elyth-post",
                    "reason_summary": "投稿操作の手順が必要。",
                }
            }
        )
        servers = service._inspection_mcp_servers(
            [
                {
                    "mcp_server_id": "elyth",
                    "client_id": "mcp-client-connector-main",
                    "transport": "streamable_http",
                    "available": True,
                    "tools": [
                        {"name": "create_post", "description": "投稿", "inputSchema": {"type": "object"}},
                    ],
                }
            ]
        )

        context = service._select_operational_skill_context(
            model_config={"provider": "test"},
            current_input={"source_kind": "user_message", "text": "投稿して"},
            capability_decision_view=[{"id": "mcp.call_tool", "mcp_servers": servers}],
        )

        self.assertEqual(context["skill_id"], "elyth-post")
        self.assertIn("# ELYTH", context["root_skill"])
        self.assertIn("# ELYTH Post", context["selected_skill"])


if __name__ == "__main__":
    unittest.main()
