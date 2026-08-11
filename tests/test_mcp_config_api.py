import unittest
from copy import deepcopy

from otomekairo.defaults import build_default_state
from otomekairo.service.common import ServiceError
from otomekairo.service.config.mixin import ServiceConfigMixin


class DummyStore:
    def __init__(self) -> None:
        self.state = build_default_state()
        self.state["console_access_token"] = "token"
        self.events = []

    def read_state(self) -> dict:
        return deepcopy(self.state)

    def write_state(self, state: dict) -> None:
        self.state = deepcopy(state)

    def append_events(self, *, events: list[dict]) -> None:
        self.events.extend(deepcopy(events))


class DummyService(ServiceConfigMixin):
    def __init__(self) -> None:
        self.store = DummyStore()

    def _now_iso(self) -> str:
        return "2026-06-20T12:00:00+09:00"


class McpConfigApiTests(unittest.TestCase):
    def test_default_state_contains_disabled_estat_mcp_template(self) -> None:
        service = DummyService()

        response = service.get_mcp_servers_editor_state("token")

        self.assertEqual(len(response["mcp_servers"]), 1)
        mcp_server = response["mcp_servers"][0]
        self.assertEqual(mcp_server["mcp_server_id"], "e-stat")
        self.assertFalse(mcp_server["enabled"])
        self.assertFalse(mcp_server["pre_send_check_required"])
        self.assertEqual(mcp_server["command"], "uvx")
        self.assertEqual(mcp_server["args"], ["estat-mcp-server"])
        self.assertEqual(mcp_server["env"]["E_STAT_APP_ID"], "")

    def test_mcp_server_public_api_masks_env(self) -> None:
        service = DummyService()

        service.replace_mcp_server(
            "token",
            "e-stat",
            {
                "enabled": True,
                "pre_send_check_required": True,
                "command": "uvx",
                "args": ["estat-mcp-server"],
                "env": {
                    "E_STAT_APP_ID": "secret",
                },
            },
        )

        response = service.get_mcp_server("token", "e-stat")
        mcp_server = response["mcp_server"]
        self.assertEqual(mcp_server["connector_kind"], "mcp_client")
        self.assertEqual(mcp_server["client_id"], "mcp-client-connector-main")
        self.assertEqual(mcp_server["transport"], "stdio")
        self.assertEqual(mcp_server["env"]["E_STAT_APP_ID"], {"value_present": True})
        self.assertNotEqual(mcp_server["env"]["E_STAT_APP_ID"], "secret")

    def test_mcp_servers_editor_state_returns_env_values(self) -> None:
        service = DummyService()

        response = service.replace_mcp_servers_editor_state(
            "token",
            {
                "mcp_servers": [
                    {
                        "mcp_server_id": "e-stat",
                        "connector_kind": "mcp_client",
                        "client_id": "mcp-client-connector-main",
                        "enabled": True,
                        "pre_send_check_required": True,
                        "transport": "stdio",
                        "command": "uvx",
                        "args": ["estat-mcp-server"],
                        "cwd": None,
                        "env": {"E_STAT_APP_ID": "secret"},
                    }
                ]
            },
        )

        self.assertEqual(response["mcp_servers"][0]["env"]["E_STAT_APP_ID"], "secret")
        self.assertEqual(service.store.events[-1]["kind"], "mcp_servers_editor_state_write")

    def test_runtime_config_filters_mcp_servers_by_client_and_enabled(self) -> None:
        service = DummyService()
        service.replace_mcp_servers_editor_state(
            "token",
            {
                "mcp_servers": [
                    {
                        "mcp_server_id": "e-stat",
                        "client_id": "mcp-client-connector-main",
                        "enabled": True,
                        "pre_send_check_required": True,
                        "command": "uvx",
                        "args": ["estat-mcp-server"],
                        "env": {"E_STAT_APP_ID": "secret"},
                    },
                    {
                        "mcp_server_id": "disabled",
                        "client_id": "mcp-client-connector-main",
                        "enabled": False,
                        "pre_send_check_required": True,
                        "command": "uvx",
                        "args": ["disabled"],
                        "env": {},
                    },
                    {
                        "mcp_server_id": "other",
                        "client_id": "mcp-client-connector-other",
                        "enabled": True,
                        "pre_send_check_required": False,
                        "command": "uvx",
                        "args": ["other"],
                        "env": {},
                    },
                ]
            },
        )

        response = service.get_connector_runtime_config("token", "mcp-client-connector-main")

        self.assertEqual(response["camera_sources"], [])
        self.assertEqual([item["mcp_server_id"] for item in response["mcp_servers"]], ["e-stat"])
        self.assertEqual(response["mcp_servers"][0]["env"]["E_STAT_APP_ID"], "secret")
        self.assertNotIn("pre_send_check_required", response["mcp_servers"][0])
        self.assertEqual(service.store.events[-1]["mcp_server_count"], 1)

    def test_mcp_server_rejects_invalid_definition(self) -> None:
        service = DummyService()

        with self.assertRaises(ServiceError) as raised:
            service.replace_mcp_server(
                "token",
                "e-stat",
                {
                    "enabled": True,
                    "pre_send_check_required": True,
                    "transport": "sse",
                    "command": "uvx",
                },
            )

        self.assertEqual(raised.exception.error_code, "unsupported_mcp_transport")


if __name__ == "__main__":
    unittest.main()
