import unittest
from copy import deepcopy
from datetime import datetime

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
        self.autonomous_runs: list[dict] = []

    def read_state(self) -> dict:
        return deepcopy(self.state)

    def list_autonomous_runs(self, *, memory_set_id: str, limit: int) -> list[dict]:
        return deepcopy(self.autonomous_runs[:limit])


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

    def _parse_iso(self, value: str) -> datetime:
        return datetime.fromisoformat(value)


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
                "autonomous_session": {
                    "enabled": True,
                    "background_enabled": True,
                    "min_interval_seconds": 3600,
                    "max_tool_calls": 10,
                },
            }
        }

    def _register_elyth_catalog(self, service: DummyService) -> None:
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
                        "mcp_server_id": "elyth",
                        "transport": "streamable_http",
                        "tools": [
                            {
                                "name": "get_notifications",
                                "description": "通知を取得する",
                                "inputSchema": {"type": "object"},
                            }
                        ],
                    }
                ],
            },
        )

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

    def test_mcp_catalog_exposes_all_server_tools_and_session_policy(self) -> None:
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

        self.assertEqual(
            [tool["name"] for tool in servers[0]["tools"]],
            ["create_post", "undeclared_tool"],
        )
        self.assertTrue(servers[0]["autonomous_session"]["enabled"])
        self.assertTrue(servers[0]["autonomous_session"]["background_eligible"])
        self.assertTrue(service._mcp_tool_is_enabled("elyth", "create_post"))
        self.assertTrue(service._mcp_tool_is_enabled("elyth", "undeclared_tool"))

    def test_background_finite_session_targets_exclude_cooldown_server_without_hiding_tools(self) -> None:
        service = DummyService()
        self._configure_elyth(service)
        self._register_elyth_catalog(service)
        service.store.autonomous_runs = [
            {
                "run_id": "autonomous_run:recent",
                "status": "completed",
                "created_at": "2026-06-20T11:30:00+09:00",
                "mcp_session": {"mcp_server_id": "elyth"},
            }
        ]

        servers = service._inspection_mcp_servers(
            service._event_stream_registry.list_capability_bindings()["mcp_servers"],
            current_time="2026-06-20T12:00:00+09:00",
        )

        self.assertTrue(servers[0]["available"])
        self.assertEqual([tool["name"] for tool in servers[0]["tools"]], ["get_notifications"])
        self.assertEqual(
            service._finite_mcp_session_targets(
                mcp_servers=servers,
                trigger_kind="background_thinking",
            ),
            [],
        )
        self.assertEqual(
            service._finite_mcp_session_targets(
                mcp_servers=servers,
                trigger_kind="user_message",
            ),
            [{"mcp_server_id": "elyth", "active_run_ids": []}],
        )

    def test_user_finite_session_target_includes_active_session_replacement_ids(self) -> None:
        service = DummyService()
        self._configure_elyth(service)
        self._register_elyth_catalog(service)
        service.store.autonomous_runs = [
            {
                "run_id": "autonomous_run:active",
                "status": "active",
                "created_at": "2026-06-20T11:30:00+09:00",
                "mcp_session": {"mcp_server_id": "elyth"},
            }
        ]

        servers = service._inspection_mcp_servers(
            service._event_stream_registry.list_capability_bindings()["mcp_servers"],
            current_time="2026-06-20T12:00:00+09:00",
        )

        self.assertEqual(
            service._finite_mcp_session_targets(
                mcp_servers=servers,
                trigger_kind="background_thinking",
            ),
            [],
        )
        self.assertEqual(
            service._finite_mcp_session_targets(
                mcp_servers=servers,
                trigger_kind="user_message",
            ),
            [{"mcp_server_id": "elyth", "active_run_ids": ["autonomous_run:active"]}],
        )

    def test_background_finite_session_target_returns_only_after_cooldown(self) -> None:
        service = DummyService()
        self._configure_elyth(service)
        self._register_elyth_catalog(service)
        service.store.autonomous_runs = [
            {
                "run_id": "autonomous_run:completed",
                "status": "completed",
                "created_at": "2026-06-20T16:52:45+09:00",
                "mcp_session": {"mcp_server_id": "elyth"},
            }
        ]
        catalog = service._event_stream_registry.list_capability_bindings()["mcp_servers"]

        for current_time in (
            "2026-06-20T16:57:42+09:00",
            "2026-06-20T17:07:42+09:00",
            "2026-06-20T17:17:42+09:00",
            "2026-06-20T17:52:44+09:00",
        ):
            with self.subTest(current_time=current_time):
                servers = service._inspection_mcp_servers(catalog, current_time=current_time)
                self.assertEqual(
                    service._finite_mcp_session_targets(
                        mcp_servers=servers,
                        trigger_kind="background_thinking",
                    ),
                    [],
                )

        servers = service._inspection_mcp_servers(
            catalog,
            current_time="2026-06-20T17:52:45+09:00",
        )
        self.assertEqual(
            service._finite_mcp_session_targets(
                mcp_servers=servers,
                trigger_kind="background_thinking",
            ),
            [{"mcp_server_id": "elyth", "active_run_ids": []}],
        )

    def test_mcp_catalog_does_not_add_skill_metadata(self) -> None:
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
                    ],
                }
            ]
        )

        self.assertNotIn("skill_id", servers[0]["tools"][0])


if __name__ == "__main__":
    unittest.main()
